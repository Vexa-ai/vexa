/**
 * MixedAudioPipeline — THE single core for single-channel (mixed) meeting
 * audio. Architecture (operator-specified): SEGMENTATION CONTROLS THE
 * CONFIRMATION BUFFER.
 *
 *   - Audio streams LIVE into the current segment buffer (the host's
 *     SpeakerStreamManager stream) — Whisper reconsiders it on its normal
 *     submit cadence, so drafts appear while the person is still talking.
 *   - A segmentation signal (diarizer commit at a pyannote boundary) labels
 *     the buffer with the commit's CLUSTER; a cluster CHANGE closes the
 *     buffer and starts the next one. The buffer is the unit; the label is
 *     revisable metadata — audio is never re-routed, never held back.
 *   - In parallel the ClusterNameBinder correlates cluster activity with the
 *     platform's "who's lit" hint timeline; segment labels resolve to real
 *     names, retroactively when evidence arrives late.
 *
 *   mixed PCM ────────────► onSegmentAudio(segKey, pcm, atMs)     [live]
 *   diarizer commit (t0..t1) = SEGMENTATION SIGNAL:
 *        1. winner = max-overlap lit name over the buffer's full span
 *        2. onSegmentLabel(segKey, winner)   [authoritative, at close]
 *        3. onSegmentClose(segKey)           [flush]
 *        4. next buffer opens at t1 (+ tail re-feed of frames after t1);
 *           provisionally labeled with the latest lit name for live drafts
 *
 * LIT-ONLY EXPERIMENT (operator-decided): segmentation cuts the buffers; the
 * who's-lit timeline only NAMES them — the winner is chosen when the buffer
 * CLOSES, never the other way around. Clustering is unused for assignment.
 */

import { OnnxLocalDiarizer, CommitEvent } from './diarization/onnx-local-diarizer';
import { ClusterNameBinder, HintKind, ResolvedAttribution } from './cluster-name-binder';

export interface MixedAudioPipelineCallbacks {
  /** Live audio for the current segment buffer — feed the host stream NOW
   *  (atMs = wall-clock the audio was spoken; stream key = segKey). */
  onSegmentAudio: (segKey: string, pcm: Float32Array, atMs: number) => void;
  /** Set/refresh the display name of a segment buffer (cluster resolved via
   *  hints, or the provisional cluster id). Idempotent. */
  onSegmentLabel: (segKey: string, displayName: string, resolution: ResolvedAttribution) => void;
  /** Segmentation closed this buffer — force-flush its stream. */
  onSegmentClose: (segKey: string) => void;
  log?: (msg: string) => void;
}

/** Keep this much recent audio for boundary tail re-feed (the frames of the
 *  NEW speaker that streamed into the old buffer before the switch-commit
 *  arrived — pyannote cadence 250 ms + utterance close ≈ ≤1 s). */
const TAIL_RING_MS = 1500;
const SAMPLE_RATE = 16000;

interface TailFrame { pcm: Float32Array; tMs: number }

export class MixedAudioPipeline {
  private diarizer: OnnxLocalDiarizer | null = null;
  private readonly binder: ClusterNameBinder;
  private readonly log: (msg: string) => void;

  private segCounter = 0;
  private currentSegKey: string;
  /** Wall-clock when the current buffer opened (first audio in it). */
  private currentSegStartMs: number | null = null;
  /** Latest lit name seen — provisional label for the OPEN buffer's drafts. */
  private lastLitName: string | null = null;
  /** Segments closed before ANY hint existed — back-filled and relabeled
   *  (rename → republish path) once hints arrive. */
  private unnamedClosed: Array<{ segKey: string; tStartMs: number; tEndMs: number }> = [];
  /** Recent frames for boundary tail re-feed. */
  private tail: TailFrame[] = [];
  private tailMs = 0;

  private constructor(private readonly cb: MixedAudioPipelineCallbacks) {
    this.log = cb.log || (() => { /* silent */ });
    this.currentSegKey = this.nextSegKey();
    this.binder = new ClusterNameBinder({});
  }

  static async create(cb: MixedAudioPipelineCallbacks): Promise<MixedAudioPipeline> {
    const p = new MixedAudioPipeline(cb);
    p.diarizer = await OnnxLocalDiarizer.create({
      // Pack's AMI-eval-tuned values — change only with eval numbers (core/eval/).
      maxUtteranceMs: 3000,
      newSpeakerThreshold: 0.55,
      veryFarThreshold: 0.90,
      newClusterCooldownMs: 2000,
      minSeedUtteranceMs: 1500,
      pyannoteInferIntervalMs: 250,
      onCommit: (ev: CommitEvent) => p.handleCommit(ev),
    });
    p.log('[MixedPipeline] diarizer ready (segmentation-driven buffers; pyannote + wespeaker)');
    return p;
  }

  /** One mixed-audio chunk — streams LIVE into the current segment buffer. */
  feedAudio(pcm: Float32Array, tsMs: number): void {
    if (this.currentSegStartMs === null) this.currentSegStartMs = tsMs;
    this.cb.onSegmentAudio(this.currentSegKey, pcm, tsMs);
    // Tail ring for boundary re-feed.
    this.tail.push({ pcm, tMs: tsMs });
    this.tailMs += (pcm.length / SAMPLE_RATE) * 1000;
    while (this.tail.length > 0 && this.tailMs > TAIL_RING_MS) {
      const f = this.tail.shift()!;
      this.tailMs -= (f.pcm.length / SAMPLE_RATE) * 1000;
    }
    if (this.diarizer) {
      this.diarizer.process(pcm, tsMs).catch((e: any) => this.log(`[MixedPipeline] process error: ${e?.message}`));
    }
  }

  /** Timestamped platform hint: who the UI showed as speaking. */
  recordHint(name: string, kind: HintKind, tMs: number, isEnd = false): void {
    this.binder.recordHint({ name, tMs, kind, isEnd });
    if (name && !isEnd) this.lastLitName = name;
    // Back-fill segments that closed before any hint existed: best overlap if
    // the hint log now covers their span, else this first-known name.
    if (name && this.unnamedClosed.length > 0) {
      for (const u of this.unnamedClosed.splice(0)) {
        const winner = this.binder.bestOverlapName({ tStartMs: u.tStartMs, tEndMs: u.tEndMs });
        const resolvedName = winner?.name ?? name;
        this.cb.onSegmentLabel(u.segKey, resolvedName, {
          speakerName: resolvedName, source: 'window-match', confidence: winner?.confidence ?? 0,
        });
        this.log(`[MixedPipeline] back-filled ${u.segKey} → "${resolvedName}"`);
      }
    }
  }

  stats(): { binder: ReturnType<ClusterNameBinder['stats']>; segments: number; lastLit: string | null } {
    return { binder: this.binder.stats(), segments: this.segCounter, lastLit: this.lastLitName };
  }

  /** Close the live buffer and reset (session end). */
  dispose(): void {
    try { this.cb.onSegmentClose(this.currentSegKey); } catch { /* best effort */ }
    try { this.diarizer?.reset(); } catch { /* best effort */ }
    this.binder.reset();
    this.tail = [];
    this.tailMs = 0;
  }

  private nextSegKey(): string {
    return `seg-${this.segCounter++}`;
  }

  /** A diarizer commit = THE segmentation signal: the utterance that just
   *  streamed into the current buffer is over. Choose the winner NOW (max
   *  lit-overlap across the buffer's full span), label, close, open next. */
  private handleCommit(ev: CommitEvent): void {
    const closingKey = this.currentSegKey;
    const spanStart = this.currentSegStartMs ?? ev.tStartMs;
    const winner = this.binder.bestOverlapName({ tStartMs: spanStart, tEndMs: ev.tEndMs });
    const name = winner?.name ?? this.lastLitName;

    if (name) {
      this.cb.onSegmentLabel(closingKey, name, {
        speakerName: name,
        source: 'window-match',
        confidence: winner?.confidence ?? 0,
      });
    } else {
      // No hint has EVER arrived — remember this segment; the first hint
      // back-fills and republishes it (rename path keeps segment_ids stable).
      this.unnamedClosed.push({ segKey: closingKey, tStartMs: spanStart, tEndMs: ev.tEndMs });
    }
    this.cb.onSegmentClose(closingKey);

    // Next buffer opens at the boundary; frames already streamed past t1
    // (commit lag) are re-fed so the new buffer starts at the true boundary.
    this.currentSegKey = this.nextSegKey();
    this.currentSegStartMs = null;
    if (this.lastLitName) {
      // Provisional label so live drafts carry a plausible name until close.
      this.cb.onSegmentLabel(this.currentSegKey, this.lastLitName, {
        speakerName: this.lastLitName,
        source: 'window-match',
        confidence: 0,
      });
    }
    for (const f of this.tail) {
      if (f.tMs >= ev.tEndMs) {
        if (this.currentSegStartMs === null) this.currentSegStartMs = f.tMs;
        this.cb.onSegmentAudio(this.currentSegKey, f.pcm, f.tMs);
      }
    }
  }
}
