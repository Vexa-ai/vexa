/**
 * ChunkedTranscriber — THE single-channel transcription core, shared by every
 * mixed-audio source (in-tab extension, bot tab-audio, Zoom, MS Teams).
 *
 * Two-tier output, exactly what the frozen WS envelope was built for
 * ({type:'transcript', speaker, confirmed[], pending[]} on
 * tc:meeting:{id}:mutable — pending is full-replace per speaker and never
 * persisted; confirmed XADDs to the collector and upserts into PG):
 *
 *   PCM frames ──► RING BUFFER (passive, audio-time indexed)
 *      │              never submitted, never trimmed mid-flight
 *      │
 *      └──► segmentation model (pyannote, via OnnxLocalDiarizer)
 *               │  commit {tStartMs, tEndMs, clusterId} — the ONLY cut signal
 *               │  commit lag is harmless: the ring holds the audio and the
 *               │  cut is applied RETROACTIVELY to an exact span
 *               ▼
 *           CUT ring[t0, t1]   (spans < MIN_CHUNK_MS carry forward;
 *               │               near-silent spans are RMS-gated)
 *               ▼
 *           one-shot Whisper per chunk (serialized FIFO, prompt-chained)
 *               │
 *               ├──► publish as PENDING — low latency (~commit lag + one
 *               │    Whisper RTT); mid-sentence cuts are fine, it's a draft
 *               ▼
 *           TURN AGGREGATION — contiguous same-cluster chunks accumulate.
 *               Turn closes on: cluster change | silence gap > TURN_GAP_MS |
 *               TURN_MAX_MS cap | dispose.
 *               ▼
 *           RESUBMIT the whole turn's audio from the ring — ONE Whisper
 *               call over the full turn → sentence-shaped segments with
 *               punctuation (Whisper segments well given full context).
 *               A chunk whose solo draft failed the quality gates is still
 *               covered here — per-chunk gating can no longer lose audio.
 *               ▼
 *           publish as CONFIRMED (clears the drafts via the envelope's
 *               replace semantics). Speaker = max-overlap lit-hint turn over
 *               the turn span (ClusterNameBinder); no evidence → the cluster
 *               id as a provisional name, renamed in place when hints arrive
 *               (same segment_ids, PG upsert).
 *
 * What is deliberately ABSENT: LocalAgreement confirmation, buffer trimming,
 * in-flight reconciliation. Audio → text is a pure function per span (chunk
 * draft + turn final); ordering and prompt chaining fall out of the
 * serialized queue. Each second of audio costs at most two Whisper calls.
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
/** Ring capacity — must hold a full turn plus commit lag. */
const RING_MS = 120_000;
/** Cap on the prompt fed to the next call (Whisper prompt window is small). */
const PROMPT_TAIL_CHARS = 200;
/** Unresolved turns kept for late hint renames. */
const MAX_UNRESOLVED = 100;
/** A silence gap between chunks longer than this closes the turn. */
const TURN_GAP_MS = 2500;
/** Hard cap on turn length — bounds finalize latency and stays well inside
 *  Whisper's input window. */
const TURN_MAX_MS = 28_000;

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
  /** Final, sentence-shaped segments for a closed turn. Persisted. */
  publish: (speaker: string, segments: ChunkSegment[]) => void;
  /** Low-latency draft state of the OPEN turn — full replace per speaker. */
  publishPending: (speaker: string, segments: ChunkSegment[]) => void;
  /** Drop a speaker's pending drafts (turn finalized under another name). */
  clearPending: (speaker: string) => void;
  /** Late hint evidence renamed a provisionally-labeled turn: republish the
   *  SAME segment ids under the new name (and clear the old name's pending). */
  rename: (oldSpeaker: string, newSpeaker: string, segments: ChunkSegment[]) => void;
  /** Explicit language (skips Whisper's language-probability gate). */
  language?: string;
  log?: (msg: string) => void;
}

interface RingFrame { pcm: Float32Array; tMs: number }
interface PendingChunk { t0: number; t1: number; clusterId: string }
interface Turn {
  clusterId: string;
  t0: number;
  t1: number;
  /** Draft segments accumulated from per-chunk transcriptions. */
  drafts: ChunkSegment[];
  /** Name the drafts were last published under (for clearing). */
  pendingName: string | null;
}
interface UnresolvedTurn { speaker: string; t0: number; t1: number; segments: ChunkSegment[] }

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

  /** Serialized work queue (chunk drafts + turn finalizations). */
  private queue: PendingChunk[] = [];
  private pumping = false;
  private lastFinalText = '';
  private chunkCounter = 0;
  private turnCounter = 0;
  private disposed = false;

  /** The open turn (drafts published as pending, finalized on close). */
  private turn: Turn | null = null;
  /** Wall-clock of the last chunk that touched the open turn — the idle
   *  timer finalizes a turn that silence (no further commits) left open. */
  private lastChunkWallMs = 0;
  private idleTimer: ReturnType<typeof setInterval> | null = null;

  /** Turns published under a provisional cluster id, awaiting hint evidence. */
  private unresolved: UnresolvedTurn[] = [];

  private constructor(private readonly cb: ChunkedTranscriberCallbacks) {
    this.log = cb.log || (() => { /* silent */ });
  }

  static async create(cb: ChunkedTranscriberCallbacks): Promise<ChunkedTranscriber> {
    const t = new ChunkedTranscriber(cb);
    // Native defaults (tab-audio tuned), NOT the old pipeline's AMI-pack
    // overrides: those forced 3s commits to bound commit lag — irrelevant
    // here (the ring makes lag free) — and split/seeded clusters far more
    // aggressively, which sprays spurious speaker_N labels on compressed
    // single-channel audio.
    t.diarizer = await OnnxLocalDiarizer.create({
      onCommit: (ev: CommitEvent) => t.handleCommit(ev),
    });
    // Silence after a turn produces no further commits, so nothing would
    // close it — finalize once the turn has been idle past the gap window.
    t.idleTimer = setInterval(() => {
      if (t.turn && !t.pumping && t.queue.length === 0
        && Date.now() - t.lastChunkWallMs > TURN_GAP_MS + 1500) {
        void t.pump(true);
      }
    }, 1000);
    t.log('[ChunkedTranscriber] ready (model-cut chunks → pending drafts → turn-final resubmission)');
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

  /** Timestamped platform hint ("who's lit"). Also re-resolves turns that
   *  published provisionally — overlap evidence only, never inheritance. */
  recordHint(name: string, kind: HintKind, tMs: number, isEnd = false): void {
    this.binder.recordHint({ name, tMs, kind, isEnd });
    if (!name || this.unresolved.length === 0) return;
    const still: UnresolvedTurn[] = [];
    for (const u of this.unresolved) {
      const winner = this.binder.bestOverlapName({ tStartMs: u.t0, tEndMs: u.t1 });
      if (winner && winner.name !== u.speaker) {
        this.cb.rename(u.speaker, winner.name, u.segments);
        this.log(`[ChunkedTranscriber] late-resolved turn [${u.t0}..${u.t1}] "${u.speaker}" → "${winner.name}"`);
      } else if (!winner) {
        still.push(u);
      }
    }
    this.unresolved = still.slice(-MAX_UNRESOLVED);
  }

  stats(): { chunks: number; turns: number; queued: number; unresolved: number; binder: ReturnType<ClusterNameBinder['stats']> } {
    return { chunks: this.chunkCounter, turns: this.turnCounter, queued: this.queue.length, unresolved: this.unresolved.length, binder: this.binder.stats() };
  }

  /** Session end: flush the carried span and finalize the open turn. The
   *  queue drains — queued chunks still draft, then the turn finalizes. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.carry && this.carry.t1 - this.carry.t0 >= 300) {
      this.queue.push(this.carry);
    }
    this.carry = null;
    if (this.idleTimer) { clearInterval(this.idleTimer); this.idleTimer = null; }
    void this.pump(true);
    try { this.diarizer?.reset(); } catch { /* best effort */ }
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
        this.queue.push(this.carry); // orphaned short span — process as-is
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

  // ── Serialized pipeline: chunk drafts + turn finalization ──────

  private async pump(finalizeAtEnd = false): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.queue.length > 0) {
        const chunk = this.queue.shift()!;
        try {
          await this.handleChunk(chunk);
        } catch (e: any) {
          this.log(`[ChunkedTranscriber] chunk [${chunk.t0}..${chunk.t1}] failed: ${e?.message}`);
        }
      }
      if ((finalizeAtEnd || this.disposed) && this.turn) {
        const t = this.turn;
        this.turn = null;
        await this.finalizeTurn(t).catch((e: any) =>
          this.log(`[ChunkedTranscriber] finalize failed: ${e?.message}`));
      }
    } finally {
      this.pumping = false;
    }
  }

  private async handleChunk(chunk: PendingChunk): Promise<void> {
    // Turn membership FIRST — a closed turn finalizes before this chunk's
    // draft publishes, keeping confirmed output strictly ordered.
    if (this.turn) {
      const closes =
        chunk.clusterId !== this.turn.clusterId ||
        chunk.t0 - this.turn.t1 > TURN_GAP_MS ||
        chunk.t1 - this.turn.t0 > TURN_MAX_MS;
      if (closes) {
        const t = this.turn;
        this.turn = null;
        await this.finalizeTurn(t);
      }
    }
    if (!this.turn) {
      this.turn = { clusterId: chunk.clusterId, t0: chunk.t0, t1: chunk.t1, drafts: [], pendingName: null };
    } else {
      this.turn.t1 = Math.max(this.turn.t1, chunk.t1);
    }
    this.lastChunkWallMs = Date.now();

    // One-shot draft for THIS chunk → pending (fast path). Gate failures are
    // fine: the audio is still inside the turn span for the final pass.
    const segments = await this.transcribeSpan(chunk.t0, chunk.t1, (i) => `mix:${this.chunkCounter}:${i}`);
    this.chunkCounter++;
    if (segments.length === 0) return;

    this.turn.drafts.push(...segments);
    const winner = this.binder.bestOverlapName({ tStartMs: this.turn.t0, tEndMs: this.turn.t1 });
    const name = winner?.name || this.turn.clusterId;
    if (this.turn.pendingName && this.turn.pendingName !== name) {
      this.cb.clearPending(this.turn.pendingName);
    }
    this.turn.pendingName = name;
    this.cb.publishPending(name, this.turn.drafts);
  }

  /** The whole turn's audio in ONE Whisper pass — sentence-shaped confirmed
   *  segments with punctuation; replaces the turn's pending drafts. */
  private async finalizeTurn(turn: Turn): Promise<void> {
    const turnId = this.turnCounter++;
    let segments = await this.transcribeSpan(turn.t0, turn.t1, (i) => `turn:${turnId}:${i}`);
    if (segments.length === 0 && turn.drafts.length > 0) {
      // Final pass produced nothing (transient service error / gate edge) —
      // promote the drafts rather than lose the turn.
      segments = turn.drafts;
      this.log(`[ChunkedTranscriber] turn ${turnId}: final pass empty — promoting ${segments.length} draft segment(s)`);
    }
    if (segments.length === 0) {
      if (turn.pendingName) this.cb.clearPending(turn.pendingName);
      return;
    }

    const winner = this.binder.bestOverlapName({ tStartMs: turn.t0, tEndMs: turn.t1 });
    const speaker = winner?.name || turn.clusterId;
    if (turn.pendingName && turn.pendingName !== speaker) {
      this.cb.clearPending(turn.pendingName);
    }
    this.cb.publish(speaker, segments);
    this.lastFinalText = segments.map(s => s.text).join(' ');
    if (!winner) {
      this.unresolved.push({ speaker, t0: turn.t0, t1: turn.t1, segments });
      if (this.unresolved.length > MAX_UNRESOLVED) this.unresolved.shift();
    }
  }

  /** One serialized Whisper pass over ring[t0..t1] with the quality gates.
   *  Returns mapped segments (possibly empty when gated). */
  private async transcribeSpan(t0: number, t1: number, segId: (i: number) => string): Promise<ChunkSegment[]> {
    const pcm = this.cut(t0, t1);
    if (pcm.length < SAMPLE_RATE * 0.2) return [];
    if (rms(pcm) < DROP_RMS) return []; // silence — never reaches Whisper

    const prompt = this.lastFinalText ? this.lastFinalText.slice(-PROMPT_TAIL_CHARS) : undefined;
    const result = await this.cb.transcribe(pcm, prompt);
    if (!result || !result.text || !result.text.trim()) return [];

    // Quality gates (the bot's production thresholds).
    const prob = result.language_probability ?? 0;
    if (!this.cb.language && prob > 0 && prob < 0.3) return [];
    const seg0 = result.segments?.[0];
    if (seg0) {
      const noSpeech = seg0.no_speech_prob ?? 0;
      const logProb = seg0.avg_logprob ?? 0;
      const compression = seg0.compression_ratio ?? 1;
      const duration = (seg0.end || 0) - (seg0.start || 0);
      if ((noSpeech > 0.5 && logProb < -0.7) || (logProb < -0.8 && duration < 2.0) || compression > 2.4) return [];
    }
    if (isHallucination(result.text)) {
      this.log(`[ChunkedTranscriber] [FILTERED] "${result.text.substring(0, 60)}"`);
      return [];
    }

    const lang = this.cb.language || result.language || 'en';
    const whisperSegs = (result.segments && result.segments.length > 0)
      ? result.segments
      : [{ text: result.text, start: 0, end: (t1 - t0) / 1000 }];

    return whisperSegs
      .map((ws, i) => ({
        text: (ws.text || '').trim(),
        startMs: t0 + (ws.start || 0) * 1000,
        endMs: Math.min(t1, t0 + (ws.end || 0) * 1000) || t1,
        language: lang,
        segmentId: segId(i),
      }))
      .filter(s => s.text && !isHallucination(s.text));
  }
}
