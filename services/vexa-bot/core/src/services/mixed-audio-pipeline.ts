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
 *   mixed PCM ──────────────► onSegmentAudio(segKey, pcm, atMs)   [live]
 *   diarizer commit (cluster, t0..t1)
 *        ├─ same cluster ──► onSegmentLabel(segKey, name)          [refresh]
 *        └─ cluster change ► onSegmentClose(segKey)                [flush]
 *                            + tail re-feed since boundary → next segKey
 *   binder late-resolve ───► onSegmentLabel for every segment of the cluster
 *
 * The diarizer is used for boundaries + embeddings + clustering only; no
 * TurnGate holding in the live path.
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
  private currentCluster: string | null = null;
  /** cluster id → segment keys labeled with it (for retroactive renames). */
  private clusterSegments = new Map<string, Set<string>>();
  /** Recent frames for boundary tail re-feed. */
  private tail: TailFrame[] = [];
  private tailMs = 0;

  private constructor(private readonly cb: MixedAudioPipelineCallbacks) {
    this.log = cb.log || (() => { /* silent */ });
    this.currentSegKey = this.nextSegKey();
    this.binder = new ClusterNameBinder({
      onLateResolve: (clusterId, resolvedName) => {
        // Rename EVERY segment this cluster labeled — published segments
        // self-correct via stable segment_id UPSERT downstream.
        const segs = this.clusterSegments.get(clusterId);
        if (!segs) return;
        this.log(`[MixedPipeline] late-resolve: ${clusterId} → "${resolvedName}" (${segs.size} segment(s))`);
        for (const segKey of segs) {
          this.cb.onSegmentLabel(segKey, resolvedName, { speakerName: resolvedName, source: 'cluster-vote', confidence: 1 });
        }
      },
    });
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
  }

  stats(): { binder: ReturnType<ClusterNameBinder['stats']>; segments: number; currentCluster: string | null } {
    return { binder: this.binder.stats(), segments: this.segCounter, currentCluster: this.currentCluster };
  }

  /** Close the live buffer and reset (session end). */
  dispose(): void {
    try { this.cb.onSegmentClose(this.currentSegKey); } catch { /* best effort */ }
    try { this.diarizer?.reset(); } catch { /* best effort */ }
    this.binder.reset();
    this.clusterSegments.clear();
    this.tail = [];
    this.tailMs = 0;
  }

  private nextSegKey(): string {
    return `seg-${this.segCounter++}`;
  }

  /** A diarizer commit = segmentation signal + cluster evidence for the audio
   *  that just streamed into the current buffer. */
  private handleCommit(ev: CommitEvent): void {
    const resolved = this.binder.resolve({ clusterId: ev.speakerId, tStartMs: ev.tStartMs, tEndMs: ev.tEndMs });

    if (this.currentCluster === null || this.currentCluster === ev.speakerId) {
      // Same voice continues — (re)label the live buffer, keep streaming.
      this.currentCluster = ev.speakerId;
      this.labelSegment(this.currentSegKey, ev.speakerId, resolved);
      return;
    }

    // Cluster changed: the speaker switched at ev.tStartMs. Close the old
    // buffer and open the next one under the new cluster. Frames spoken by
    // the NEW speaker that already streamed into the old buffer (commit lag)
    // are re-fed from the tail ring so the new buffer starts at the true
    // boundary; the old buffer's unconfirmed tail overlap is Whisper noise at
    // worst (a word), never lost audio.
    const oldKey = this.currentSegKey;
    this.cb.onSegmentClose(oldKey);
    this.currentSegKey = this.nextSegKey();
    this.currentCluster = ev.speakerId;
    this.labelSegment(this.currentSegKey, ev.speakerId, resolved);
    for (const f of this.tail) {
      if (f.tMs >= ev.tStartMs) this.cb.onSegmentAudio(this.currentSegKey, f.pcm, f.tMs);
    }
  }

  private labelSegment(segKey: string, clusterId: string, resolved: ResolvedAttribution): void {
    if (!this.clusterSegments.has(clusterId)) this.clusterSegments.set(clusterId, new Set());
    this.clusterSegments.get(clusterId)!.add(segKey);
    this.cb.onSegmentLabel(segKey, resolved.speakerName, resolved);
  }
}
