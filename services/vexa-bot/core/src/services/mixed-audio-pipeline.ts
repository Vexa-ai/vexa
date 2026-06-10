/**
 * MixedAudioPipeline — THE single core for single-channel (mixed) meeting
 * audio. Every consumer that receives one stream carrying all remote
 * participants (Zoom web — bot PulseAudio or extension tabCapture; MS Teams
 * downlink) runs this exact pipeline:
 *
 *   mixed 16 kHz PCM ─→ OnnxLocalDiarizer (pyannote boundaries + wespeaker
 *   embeddings + online clustering) ─→ commits ─→ TurnGate (turns stabilize
 *   before naming; wrong names cosmetic, audio never cross-routed)
 *                                         │
 *   platform hints (DOM active-speaker /  ▼
 *   captions / voice-outline) ──→ ClusterNameBinder.resolve(turn)
 *                                         │
 *                                         ▼
 *                       onTurn(clusterId, resolvedName, audio, source)
 *                       onRename(clusterId, resolvedName)   [late-resolve]
 *
 * The host owns the SpeakerStreamManager (and everything downstream:
 * TranscriptionClient → SegmentPublisher) and wires:
 *   onTurn   → ensure speaker stream `clusterId` named `resolvedName`,
 *              then speakerManager.feedAudio(clusterId, audio)
 *   onRename → speakerManager.updateSpeakerName(clusterId, name)
 *
 * Diarizer/clustering thresholds are the pack's OFFLINE-EVAL-derived values
 * (AMI corpus sweep — see pack-msteams-diarization-cutover); change them only
 * with eval numbers from core/eval/.
 */

import { OnnxLocalDiarizer, CommitEvent } from './diarization/onnx-local-diarizer';
import { TurnGate, DEFAULT_TURN_GATE } from './diarization/turn-gate';
import { ClusterNameBinder, HintKind, ResolvedAttribution } from './cluster-name-binder';

export interface MixedAudioPipelineCallbacks {
  /** A named turn is ready: feed `audio` into the speaker stream `clusterId`
   *  displayed as `resolvedName` (provisional cluster id until hints bind). */
  onTurn: (clusterId: string, resolvedName: string, audio: Float32Array, resolution: ResolvedAttribution) => void;
  /** A previously-provisional cluster gained a real name — rename its stream
   *  (already-published segments self-correct via stable segment_id UPSERT). */
  onRename: (clusterId: string, resolvedName: string) => void;
  log?: (msg: string) => void;
}

export class MixedAudioPipeline {
  private diarizer: OnnxLocalDiarizer | null = null;
  private readonly turnGate: TurnGate;
  private readonly binder: ClusterNameBinder;
  private pendingFrames: Float32Array[] = [];
  private readonly log: (msg: string) => void;

  private constructor(private readonly cb: MixedAudioPipelineCallbacks) {
    this.log = cb.log || (() => { /* silent */ });
    this.binder = new ClusterNameBinder({
      onLateResolve: (clusterId, resolvedName) => {
        this.log(`[MixedPipeline] late-resolve: ${clusterId} → "${resolvedName}"`);
        this.cb.onRename(clusterId, resolvedName);
      },
    });
    this.turnGate = new TurnGate(DEFAULT_TURN_GATE, (clusterId, audio, tStartMs) => {
      const tEndMs = tStartMs + (audio.length / 16000) * 1000;
      const resolved = this.binder.resolve({ clusterId, tStartMs, tEndMs });
      this.cb.onTurn(clusterId, resolved.speakerName, audio, resolved);
    });
  }

  static async create(cb: MixedAudioPipelineCallbacks): Promise<MixedAudioPipeline> {
    const p = new MixedAudioPipeline(cb);
    p.diarizer = await OnnxLocalDiarizer.create({
      maxUtteranceMs: 3000,
      newSpeakerThreshold: 0.55,
      veryFarThreshold: 0.90,
      newClusterCooldownMs: 2000,
      minSeedUtteranceMs: 1500,
      pyannoteInferIntervalMs: 250,
      onCommit: (ev: CommitEvent) => p.handleCommit(ev),
    });
    p.log('[MixedPipeline] diarizer ready (pyannote-segmentation-3.0 + wespeaker)');
    return p;
  }

  /** One mixed-audio chunk (16 kHz mono Float32). tsMs: host wall-clock. */
  feedAudio(pcm: Float32Array, tsMs: number): void {
    this.pendingFrames.push(pcm);
    if (this.diarizer) {
      this.diarizer.process(pcm, tsMs).catch((e: any) => this.log(`[MixedPipeline] process error: ${e?.message}`));
    } else if (this.pendingFrames.length > 2000) {
      // Safety valve while the diarizer loads (~200 ms) — never grow unbounded.
      this.pendingFrames.splice(0, this.pendingFrames.length - 2000);
    }
  }

  /** Timestamped platform hint: who the UI showed as speaking. */
  recordHint(name: string, kind: HintKind, tMs: number, isEnd = false): void {
    this.binder.recordHint({ name, tMs, kind, isEnd });
  }

  /** Diagnostics for telemetry. */
  stats(): { binder: ReturnType<ClusterNameBinder['stats']>; pendingFrames: number } {
    return { binder: this.binder.stats(), pendingFrames: this.pendingFrames.length };
  }

  /** Flush the held turn and reset all state (session end). */
  dispose(): void {
    try { this.turnGate.finish(); } catch { /* best effort */ }
    try { this.diarizer?.reset(); } catch { /* best effort */ }
    this.binder.reset();
    this.pendingFrames.length = 0;
  }

  /** Drain this commit's frames (FIFO; commits arrive in order) and hand
   *  (embedding, audio) to the TurnGate — verbatim drain from the pack. */
  private handleCommit(ev: CommitEvent): void {
    const MIN_TOTAL_SAMPLES = Math.ceil(0.2 * 16000); // Whisper needs ≥200 ms
    const wantSamples = Math.round(((ev.tEndMs - ev.tStartMs) / 1000) * 16000);
    const inRange: Float32Array[] = [];
    let drained = 0;
    let collected = 0;
    while (drained < this.pendingFrames.length && collected < wantSamples) {
      const pcm = this.pendingFrames[drained];
      inRange.push(pcm);
      collected += pcm.length;
      drained++;
    }
    if (drained > 0) this.pendingFrames.splice(0, drained);
    if (collected < MIN_TOTAL_SAMPLES) return;
    let audio: Float32Array;
    if (inRange.length === 1) audio = inRange[0];
    else {
      audio = new Float32Array(collected);
      let o = 0;
      for (const p of inRange) { audio.set(p, o); o += p.length; }
    }
    this.turnGate.onCommit(new Float32Array(ev.emb), audio, ev.tStartMs, ev.tEndMs);
  }
}
