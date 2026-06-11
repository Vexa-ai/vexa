/**
 * ChunkedTranscriber — THE single-channel transcription core, shared by every
 * mixed-audio source (in-tab extension, bot tab-audio, Zoom, MS Teams).
 *
 * Shape (vexa-desktop's proven topology, with the segmentation model as the
 * sole cutter):
 *
 *   PCM frames ──► RING BUFFER (passive, audio-time indexed)
 *      │              never submitted, never trimmed mid-flight
 *      │
 *      └──► segmentation model (pyannote, via OnnxLocalDiarizer)
 *               │  commit {tStartMs, tEndMs} — the ONLY cut signal
 *               │  commit lag is harmless: the ring holds the audio and the
 *               │  cut is applied RETROACTIVELY to an exact span
 *               ▼
 *           CUT ring[t0, t1]
 *               · spans shorter than MIN_CHUNK_MS carry into the next
 *                 contiguous span (no sub-second Whisper calls)
 *               · RMS gate: near-silent spans never reach Whisper
 *               ▼
 *           ONE Whisper call per chunk — strictly serialized FIFO,
 *               initial_prompt = the previous chunk's emitted text
 *               ▼
 *           speaker = max-overlap lit-hint turn over [t0, t1]
 *               (ClusterNameBinder as the hint-timeline matcher; no overlap
 *                evidence → the diarizer's cluster id as a provisional name,
 *                renamed in place when hints arrive — same segment_ids)
 *               ▼
 *           EMIT ONCE, immutable → host publishes (WS envelope unchanged)
 *
 * What is deliberately ABSENT: live drafts, LocalAgreement confirmation,
 * buffer trimming, in-flight reconciliation. Audio → text is a pure function
 * per chunk; ordering and prompt chaining fall out of the serialized queue.
 */

import { OnnxLocalDiarizer, CommitEvent } from './diarization/onnx-local-diarizer';
import { ClusterNameBinder, HintKind } from './cluster-name-binder';
import { TranscriptionResult } from './transcription-client';
import { isHallucination } from './hallucination-filter';

const SAMPLE_RATE = 16000;
/** Spans shorter than this carry into the next contiguous span. */
const MIN_CHUNK_MS = 700;
/** A carried span merges with the next one only if the gap is below this. */
const MERGE_GAP_MS = 1000;
/** Near-silent chunks are dropped before Whisper (desktop's DROP_RMS). */
const DROP_RMS = 0.006;
/** Ring capacity — bounds memory; far above any observed commit lag. */
const RING_MS = 120_000;
/** Cap on the prompt fed to the next chunk (Whisper prompt window is small). */
const PROMPT_TAIL_CHARS = 200;
/** Unresolved chunks kept for late hint renames. */
const MAX_UNRESOLVED = 100;

export interface ChunkSegment {
  text: string;
  /** Audio-time ms (same timebase as feedAudio's tsMs). */
  startMs: number;
  endMs: number;
  language: string;
  /** Stable suffix — host prefixes its session uid. Same id on rename. */
  segmentId: string;
}

export interface ChunkedTranscriberCallbacks {
  /** One Whisper round-trip. Called strictly serially. */
  transcribe: (pcm: Float32Array, prompt?: string) => Promise<TranscriptionResult>;
  /** Emit a chunk's segments, once, immutable. */
  publish: (speaker: string, segments: ChunkSegment[]) => void;
  /** Late hint evidence renamed a provisionally-labeled chunk: republish the
   *  SAME segment ids under the new name (and clear the old name's pending). */
  rename: (oldSpeaker: string, newSpeaker: string, segments: ChunkSegment[]) => void;
  /** Explicit language (skips Whisper's language-probability gate). */
  language?: string;
  log?: (msg: string) => void;
}

interface RingFrame { pcm: Float32Array; tMs: number }
interface PendingChunk { t0: number; t1: number; clusterId: string }
interface UnresolvedChunk { speaker: string; t0: number; t1: number; segments: ChunkSegment[] }

function rms(s: Float32Array): number {
  if (s.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < s.length; i++) sum += s[i] * s[i];
  return Math.sqrt(sum / s.length);
}

export class ChunkedTranscriber {
  private diarizer: OnnxLocalDiarizer | null = null;
  private readonly binder = new ClusterNameBinder({});
  private readonly log: (msg: string) => void;

  private ring: RingFrame[] = [];
  private ringMs = 0;

  /** Short span carried forward to merge with the next contiguous commit. */
  private carry: PendingChunk | null = null;

  /** Serialized transcription queue. */
  private queue: PendingChunk[] = [];
  private pumping = false;
  private lastEmittedText = '';
  private chunkCounter = 0;
  private disposed = false;

  /** Chunks published under a provisional cluster id, awaiting hint evidence. */
  private unresolved: UnresolvedChunk[] = [];

  private constructor(private readonly cb: ChunkedTranscriberCallbacks) {
    this.log = cb.log || (() => { /* silent */ });
  }

  static async create(cb: ChunkedTranscriberCallbacks): Promise<ChunkedTranscriber> {
    const t = new ChunkedTranscriber(cb);
    t.diarizer = await OnnxLocalDiarizer.create({
      // Pack's AMI-eval-tuned values — change only with eval numbers (core/eval/).
      maxUtteranceMs: 3000,
      newSpeakerThreshold: 0.55,
      veryFarThreshold: 0.90,
      newClusterCooldownMs: 2000,
      minSeedUtteranceMs: 1500,
      pyannoteInferIntervalMs: 250,
      onCommit: (ev: CommitEvent) => t.handleCommit(ev),
    });
    t.log('[ChunkedTranscriber] ready (model-cut chunks, one-shot serialized transcription)');
    return t;
  }

  /** One mixed-audio frame. Ring + segmentation model — nothing else. */
  feedAudio(pcm: Float32Array, tsMs: number): void {
    if (this.disposed) return;
    this.ring.push({ pcm, tMs: tsMs });
    this.ringMs += (pcm.length / SAMPLE_RATE) * 1000;
    while (this.ring.length > 0 && this.ringMs > RING_MS) {
      const f = this.ring.shift()!;
      this.ringMs -= (f.pcm.length / SAMPLE_RATE) * 1000;
    }
    this.diarizer?.process(pcm, tsMs).catch((e: any) =>
      this.log(`[ChunkedTranscriber] diarizer error: ${e?.message}`));
  }

  /** Timestamped platform hint ("who's lit"). Also re-resolves chunks that
   *  published provisionally — overlap evidence only, never inheritance. */
  recordHint(name: string, kind: HintKind, tMs: number, isEnd = false): void {
    this.binder.recordHint({ name, tMs, kind, isEnd });
    if (!name || this.unresolved.length === 0) return;
    const still: UnresolvedChunk[] = [];
    for (const u of this.unresolved) {
      const winner = this.binder.bestOverlapName({ tStartMs: u.t0, tEndMs: u.t1 });
      if (winner && winner.name !== u.speaker) {
        this.cb.rename(u.speaker, winner.name, u.segments);
        this.log(`[ChunkedTranscriber] late-resolved [${u.t0}..${u.t1}] "${u.speaker}" → "${winner.name}"`);
      } else if (!winner) {
        still.push(u);
      }
    }
    this.unresolved = still.slice(-MAX_UNRESOLVED);
  }

  stats(): { chunks: number; queued: number; unresolved: number; binder: ReturnType<ClusterNameBinder['stats']> } {
    return { chunks: this.chunkCounter, queued: this.queue.length, unresolved: this.unresolved.length, binder: this.binder.stats() };
  }

  /** Session end: flush any carried span, stop cutting. The queue drains —
   *  in-flight and queued chunks still emit. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.carry && this.carry.t1 - this.carry.t0 >= 300) {
      this.queue.push(this.carry);
      void this.pump();
    }
    this.carry = null;
    try { this.diarizer?.reset(); } catch { /* best effort */ }
    this.binder.reset();
    this.ring = [];
    this.ringMs = 0;
  }

  // ── Cutting ────────────────────────────────────────────────────

  /** A segmentation commit = the ONLY cut signal. Spans merge forward until
   *  they clear MIN_CHUNK_MS, then enter the serialized queue. */
  private handleCommit(ev: CommitEvent): void {
    if (this.disposed) return;
    let t0 = ev.tStartMs;
    const t1 = ev.tEndMs;
    if (t1 <= t0) return;

    if (this.carry) {
      if (t0 - this.carry.t1 <= MERGE_GAP_MS) {
        t0 = this.carry.t0; // contiguous — absorb the carried span
      } else if (this.carry.t1 - this.carry.t0 >= 300) {
        this.queue.push(this.carry); // orphaned short span — transcribe as-is
      }
      this.carry = null;
    }

    if (t1 - t0 < MIN_CHUNK_MS) {
      this.carry = { t0, t1, clusterId: ev.speakerId };
      return;
    }

    this.queue.push({ t0, t1, clusterId: ev.speakerId });
    void this.pump();
  }

  /** Exact retroactive cut from the ring (frames are contiguous per source;
   *  partial frames at the edges are sliced by sample offset). */
  private cut(t0: number, t1: number): Float32Array {
    const parts: Float32Array[] = [];
    let total = 0;
    for (const f of this.ring) {
      const fStart = f.tMs;
      const fEnd = f.tMs + (f.pcm.length / SAMPLE_RATE) * 1000;
      if (fEnd <= t0) continue;
      if (fStart >= t1) break;
      const from = Math.max(0, Math.round(((t0 - fStart) / 1000) * SAMPLE_RATE));
      const to = Math.min(f.pcm.length, Math.round(((t1 - fStart) / 1000) * SAMPLE_RATE));
      if (to > from) { parts.push(f.pcm.subarray(from, to)); total += to - from; }
    }
    const out = new Float32Array(total);
    let off = 0;
    for (const p of parts) { out.set(p, off); off += p.length; }
    return out;
  }

  // ── Serialized one-shot transcription ──────────────────────────

  private async pump(): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.queue.length > 0) {
        const chunk = this.queue.shift()!;
        try {
          await this.transcribeChunk(chunk);
        } catch (e: any) {
          this.log(`[ChunkedTranscriber] chunk [${chunk.t0}..${chunk.t1}] failed: ${e?.message}`);
        }
      }
    } finally {
      this.pumping = false;
    }
  }

  private async transcribeChunk(chunk: PendingChunk): Promise<void> {
    const pcm = this.cut(chunk.t0, chunk.t1);
    if (pcm.length < SAMPLE_RATE * 0.2) return;
    if (rms(pcm) < DROP_RMS) return; // silence — never reaches Whisper

    const prompt = this.lastEmittedText
      ? this.lastEmittedText.slice(-PROMPT_TAIL_CHARS)
      : undefined;
    const result = await this.cb.transcribe(pcm, prompt);
    if (!result || !result.text || !result.text.trim()) return;

    // Quality gates (the bot's production thresholds, applied once per chunk).
    const prob = result.language_probability ?? 0;
    if (!this.cb.language && prob > 0 && prob < 0.3) return;
    const seg0 = result.segments?.[0];
    if (seg0) {
      const noSpeech = seg0.no_speech_prob ?? 0;
      const logProb = seg0.avg_logprob ?? 0;
      const compression = seg0.compression_ratio ?? 1;
      const duration = (seg0.end || 0) - (seg0.start || 0);
      if ((noSpeech > 0.5 && logProb < -0.7) || (logProb < -0.8 && duration < 2.0) || compression > 2.4) return;
    }
    if (isHallucination(result.text)) {
      this.log(`[ChunkedTranscriber] [FILTERED] "${result.text.substring(0, 60)}"`);
      return;
    }

    const chunkId = this.chunkCounter++;
    const lang = this.cb.language || result.language || 'en';
    const whisperSegs = (result.segments && result.segments.length > 0)
      ? result.segments
      : [{ text: result.text, start: 0, end: (chunk.t1 - chunk.t0) / 1000 }];

    const segments: ChunkSegment[] = whisperSegs
      .map((ws, i) => ({
        text: (ws.text || '').trim(),
        startMs: chunk.t0 + (ws.start || 0) * 1000,
        endMs: Math.min(chunk.t1, chunk.t0 + (ws.end || 0) * 1000) || chunk.t1,
        language: lang,
        segmentId: `mix:${chunkId}:${i}`,
      }))
      .filter(s => s.text && !isHallucination(s.text));
    if (segments.length === 0) return;

    // WHO: max-overlap lit-hint turn over the chunk's span. The chunk is
    // single-speaker by construction (the model cut it), so attribution is
    // chunk-level. No evidence yet → provisional cluster id, renamed in
    // place when hints cover the span.
    const winner = this.binder.bestOverlapName({ tStartMs: chunk.t0, tEndMs: chunk.t1 });
    const speaker = winner?.name || chunk.clusterId;

    this.cb.publish(speaker, segments);
    this.lastEmittedText = segments.map(s => s.text).join(' ');
    if (!winner) {
      this.unresolved.push({ speaker, t0: chunk.t0, t1: chunk.t1, segments });
      if (this.unresolved.length > MAX_UNRESOLVED) this.unresolved.shift();
    }
  }
}
