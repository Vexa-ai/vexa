/**
 * ChunkedTranscriber — THE single-channel transcription core, shared by every
 * mixed-audio source (in-tab extension, bot tab-audio, Zoom, MS Teams).
 *
 * Output rides the frozen WS envelope ({type:'transcript', speaker,
 * confirmed[], pending[]} on tc:meeting:{id}:mutable — pending is
 * full-replace per speaker and never persisted; confirmed XADDs to the
 * collector and upserts into PG):
 *
 *   PCM frames ──► RING BUFFER (passive, audio-time indexed)
 *      │              never submitted, never trimmed mid-flight
 *      │
 *      └──► segmentation model (pyannote, via OnnxLocalDiarizer)
 *               │  commit {tStartMs, tEndMs, clusterId} — the ONLY cut signal
 *               │  commit lag is harmless: the ring holds the audio and the
 *               │  cut is applied RETROACTIVELY to an exact span
 *               ▼
 *           TURN — contiguous same-cluster commits. Closes on cluster
 *               change, silence gap > TURN_GAP_MS, an unconfirmed window
 *               past TURN_MAX_MS, idle timeout, or dispose.
 *               ▼
 *           CONTINUOUS CONFIRMATION (the bot's LocalAgreement-2, ported
 *           from SpeakerStreamManager.handleTranscriptionResult):
 *               every commit resubmits the turn's UNCONFIRMED window
 *               [confirmedUpTo..t1] from the ring — one serialized Whisper
 *               call. Leading whisper segments whose words are STABLE
 *               across two consecutive submissions CONFIRM immediately
 *               (sentence-shaped, punctuated — whisper segments well with
 *               growing context); the still-forming tail publishes as
 *               PENDING. Confirmation advances the window, so long
 *               monologues confirm continuously — nobody waits for the
 *               turn to end.
 *               ▼
 *           On turn close: one last submission of the remaining window,
 *               everything confirms (last chance), pending clears. An empty
 *               final pass promotes the pending tail — turns are never lost.
 *
 *   WHO: speaker = max-overlap lit-hint turn over the turn span
 *        (ClusterNameBinder); no evidence → the diarizer's cluster id as a
 *        provisional name, renamed in place when hints arrive (same
 *        segment_ids, PG upsert).
 *
 * Prompt chaining: every submission carries the tail of the confirmed text
 * (across turns too) as initial_prompt.
 *
 * What is deliberately ABSENT: live mutable buffers and trim/in-flight
 * reconciliation. Audio→text is a pure function of the ring span; ordering
 * falls out of the strictly serialized queue.
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
/** Near-silent spans are dropped before Whisper (desktop's DROP_RMS). */
const DROP_RMS = 0.006;
/** Ring capacity — must hold a full unconfirmed window plus commit lag. */
const RING_MS = 120_000;
/** Cap on the prompt fed to the next call (Whisper prompt window is small). */
const PROMPT_TAIL_CHARS = 200;
/** Unresolved turns kept for late hint renames. */
const MAX_UNRESOLVED = 100;
/** A silence gap between commits longer than this closes the turn. */
const TURN_GAP_MS = 2500;
/** Cap on the UNCONFIRMED window — if stability stalls this long, the turn
 *  force-closes (everything confirms). Stays inside Whisper's input window. */
const TURN_MAX_MS = 28_000;
/** Don't bother Whisper with unconfirmed windows shorter than this unless
 *  the turn is closing. */
const MIN_SUBMIT_MS = 800;

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
  /** Confirmed, sentence-shaped segments. Persisted. */
  publish: (speaker: string, segments: ChunkSegment[]) => void;
  /** Low-latency still-forming tail of the open turn — full replace per speaker. */
  publishPending: (speaker: string, segments: ChunkSegment[]) => void;
  /** Drop a speaker's pending drafts (turn moved to another name / closed). */
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
  turnId: number;
  t0: number;
  /** Latest committed end (audio ms). */
  t1: number;
  /** Audio confirmed & published up to here. */
  confirmedUpToMs: number;
  /** Previous submission's words (LocalAgreement-2). Reset on confirm. */
  lastWords: string[];
  /** Confirmed-segment counter → stable ids turn:{turnId}:{seq}. */
  seq: number;
  /** Everything confirmed in this turn — for late hint renames. */
  allConfirmed: ChunkSegment[];
  /** Name the pending tail was last published under. */
  pendingName: string | null;
  /** Last unconfirmed tail — promoted if the closing pass returns nothing. */
  pendingTail: ChunkSegment[];
}
interface UnresolvedTurn { speaker: string; t0: number; t1: number; segments: ChunkSegment[] }

function rms(s: Float32Array): number {
  if (s.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < s.length; i++) sum += s[i] * s[i];
  return Math.sqrt(sum / s.length);
}

function words(text: string): string[] {
  return text.trim().split(/\s+/).filter(w => w.length > 0);
}

export class ChunkedTranscriber {
  private diarizer: OnnxLocalDiarizer | null = null;
  private readonly binder = new ClusterNameBinder({});
  private readonly log: (msg: string) => void;

  private ring: RingFrame[] = [];
  private ringMs = 0;

  /** Short span carried forward to merge with the next contiguous commit. */
  private carry: PendingChunk | null = null;
  /** First audio frame's timestamp — the FIRST commit back-extends to here
   *  (bounded): the model needs seconds to lock on, but speech from t=0 is
   *  already in the ring. Without this the session opens with a hole. */
  private firstAudioMs: number | null = null;
  private firstCommitSeen = false;

  /** Serialized work queue (commit-triggered submissions). */
  private queue: PendingChunk[] = [];
  private pumping = false;
  private lastConfirmedText = '';
  private commitCounter = 0;
  private turnCounter = 0;
  private disposed = false;

  /** The open turn. */
  private turn: Turn | null = null;
  /** Wall-clock of the last commit that touched the open turn — the idle
   *  timer closes a turn that silence (no further commits) left open. */
  private lastChunkWallMs = 0;
  private idleTimer: ReturnType<typeof setInterval> | null = null;

  /** Turns published under a provisional cluster id, awaiting hint evidence. */
  private unresolved: UnresolvedTurn[] = [];

  private constructor(private readonly cb: ChunkedTranscriberCallbacks) {
    this.log = cb.log || (() => { /* silent */ });
  }

  static async create(cb: ChunkedTranscriberCallbacks): Promise<ChunkedTranscriber> {
    const t = new ChunkedTranscriber(cb);
    // Native defaults (tab-audio tuned) — see commit history for why the old
    // pipeline's AMI-pack overrides are wrong here.
    t.diarizer = await OnnxLocalDiarizer.create({
      onCommit: (ev: CommitEvent) => t.handleCommit(ev),
    });
    // Silence after a turn produces no further commits, so nothing would
    // close it — close once the turn has been idle past the gap window.
    t.idleTimer = setInterval(() => {
      if (t.turn && !t.pumping && t.queue.length === 0
        && Date.now() - t.lastChunkWallMs > TURN_GAP_MS + 1500) {
        void t.pump(true);
      }
    }, 1000);
    t.log('[ChunkedTranscriber] ready (model-cut turns, continuous LocalAgreement-2 confirmation)');
    return t;
  }

  /** One mixed-audio frame. Ring + segmentation model — nothing else. */
  feedAudio(pcm: Float32Array, tsMs: number): void {
    if (this.disposed) return;
    if (this.firstAudioMs === null) this.firstAudioMs = tsMs;
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

  stats(): { commits: number; turns: number; queued: number; unresolved: number; binder: ReturnType<ClusterNameBinder['stats']> } {
    return { commits: this.commitCounter, turns: this.turnCounter, queued: this.queue.length, unresolved: this.unresolved.length, binder: this.binder.stats() };
  }

  /** Session end: flush the carried span and close the open turn. */
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

    // Cold start: the model needs seconds of audio to lock on, so its first
    // commit opens well after capture began — but the speech from frame one
    // is in the ring. Back-extend the FIRST commit to the first audio frame
    // (bounded; leading silence is harmless to Whisper and the RMS gate
    // still protects a truly silent prefix-heavy span).
    if (!this.firstCommitSeen) {
      this.firstCommitSeen = true;
      if (this.firstAudioMs !== null && t0 - this.firstAudioMs > 0 && t0 - this.firstAudioMs <= 12_000) {
        this.log(`[ChunkedTranscriber] first commit back-extended ${t0 - this.firstAudioMs}ms to session start`);
        t0 = this.firstAudioMs;
      }
    }

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

  // ── Serialized pipeline ────────────────────────────────────────

  private async pump(closeAtEnd = false): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.queue.length > 0) {
        const chunk = this.queue.shift()!;
        try {
          await this.handleChunk(chunk);
        } catch (e: any) {
          this.log(`[ChunkedTranscriber] commit [${chunk.t0}..${chunk.t1}] failed: ${e?.message}`);
        }
      }
      if ((closeAtEnd || this.disposed) && this.turn) {
        const t = this.turn;
        this.turn = null;
        await this.submitTurn(t, true).catch((e: any) =>
          this.log(`[ChunkedTranscriber] turn close failed: ${e?.message}`));
      }
    } finally {
      this.pumping = false;
    }
  }

  private async handleChunk(chunk: PendingChunk): Promise<void> {
    this.commitCounter++;
    // Turn membership FIRST — a closed turn fully confirms before the next
    // turn's first submission, keeping confirmed output strictly ordered.
    if (this.turn) {
      const closes =
        chunk.clusterId !== this.turn.clusterId ||
        chunk.t0 - this.turn.t1 > TURN_GAP_MS ||
        chunk.t1 - this.turn.confirmedUpToMs > TURN_MAX_MS;
      if (closes) {
        const t = this.turn;
        this.turn = null;
        await this.submitTurn(t, true);
      }
    }
    if (!this.turn) {
      this.turn = {
        clusterId: chunk.clusterId, turnId: this.turnCounter++,
        t0: chunk.t0, t1: chunk.t1, confirmedUpToMs: chunk.t0,
        lastWords: [], seq: 0, allConfirmed: [], pendingName: null, pendingTail: [],
      };
    } else {
      this.turn.t1 = Math.max(this.turn.t1, chunk.t1);
    }
    this.lastChunkWallMs = Date.now();

    await this.submitTurn(this.turn, false);
  }

  /** One submission of the turn's unconfirmed window. While the turn is open,
   *  confirmation is LocalAgreement-2 (the bot's word-prefix stability); on
   *  close everything confirms. */
  private async submitTurn(turn: Turn, closing: boolean): Promise<void> {
    const spanStart = turn.confirmedUpToMs;
    const spanEnd = turn.t1;
    if (spanEnd - spanStart < (closing ? 250 : MIN_SUBMIT_MS)) {
      if (closing) this.closeOut(turn);
      return;
    }

    const pcm = this.cut(spanStart, spanEnd);
    if (pcm.length < SAMPLE_RATE * 0.2 || rms(pcm) < DROP_RMS) {
      if (closing) this.closeOut(turn);
      return;
    }

    const prompt = this.lastConfirmedText ? this.lastConfirmedText.slice(-PROMPT_TAIL_CHARS) : undefined;
    let result: TranscriptionResult | null = null;
    try {
      result = await this.cb.transcribe(pcm, prompt);
    } catch (e: any) {
      this.log(`[ChunkedTranscriber] transcribe failed: ${e?.message}`);
    }
    const gated = result ? this.applyGates(result, spanEnd - spanStart) : null;
    if (!gated || gated.length === 0) {
      if (closing) this.closeOut(turn);
      return;
    }

    // Map whisper segments (relative to spanStart) to audio time.
    const lang = this.cb.language || result!.language || 'en';
    const mapped = gated.map((ws) => ({
      text: ws.text.trim(),
      startMs: spanStart + (ws.start || 0) * 1000,
      endMs: Math.min(spanEnd, spanStart + (ws.end || 0) * 1000) || spanEnd,
      language: lang,
      relEnd: ws.end || 0,
    })).filter(s => {
      if (!s.text) return false;
      // Prompt echo — whisper parroting the initial_prompt back. Targeted
      // check; the blanket phrase list would also kill legit short answers
      // ("Yes.") inside real-speech windows the RMS gate already vouched for.
      if (prompt && s.text.length > 6 && prompt.includes(s.text)) return false;
      return true;
    });
    if (mapped.length === 0) {
      if (closing) this.closeOut(turn);
      return;
    }

    let confirmCount: number;
    if (closing) {
      confirmCount = mapped.length; // last chance — everything confirms
    } else {
      // LocalAgreement-2 (ported from SpeakerStreamManager): longest common
      // word prefix across consecutive submissions; confirm whole segments
      // fully inside the stable prefix, never the still-forming tail.
      const currentWords = mapped.flatMap(s => words(s.text));
      const prevWords = turn.lastWords;
      let prefixLen = 0;
      const maxLen = Math.min(currentWords.length, prevWords.length);
      for (let i = 0; i < maxLen; i++) {
        if (currentWords[i] === prevWords[i]) prefixLen = i + 1;
        else break;
      }
      confirmCount = 0;
      if (prefixLen > 0 && prefixLen < currentWords.length) {
        let remaining = prefixLen;
        for (const s of mapped) {
          const n = words(s.text).length;
          if (remaining >= n) { remaining -= n; confirmCount++; }
          else break; // partial segment — don't emit partial
        }
      }
      turn.lastWords = confirmCount > 0 ? [] : currentWords; // bot resets on advance
    }

    const name = this.resolveName(turn);

    if (confirmCount > 0) {
      const confirmed: ChunkSegment[] = mapped.slice(0, confirmCount).map(s => ({
        text: s.text, startMs: s.startMs, endMs: s.endMs, language: s.language,
        segmentId: `turn:${turn.turnId}:${turn.seq++}`,
      }));
      if (turn.pendingName && turn.pendingName !== name) this.cb.clearPending(turn.pendingName);
      this.cb.publish(name, confirmed);
      turn.allConfirmed.push(...confirmed);
      turn.confirmedUpToMs = spanStart + mapped[confirmCount - 1].relEnd * 1000;
      const txt = confirmed.map(s => s.text).join(' ');
      this.lastConfirmedText = (this.lastConfirmedText + ' ' + txt).slice(-PROMPT_TAIL_CHARS * 2);
    }

    const tail: ChunkSegment[] = mapped.slice(confirmCount).map((s, i) => ({
      text: s.text, startMs: s.startMs, endMs: s.endMs, language: s.language,
      segmentId: `turn:${turn.turnId}:p${i}`,
    }));

    if (closing) {
      this.closeOut(turn);
    } else {
      turn.pendingTail = tail;
      if (tail.length > 0) {
        if (turn.pendingName && turn.pendingName !== name) this.cb.clearPending(turn.pendingName);
        turn.pendingName = name;
        this.cb.publishPending(name, tail);
      } else if (turn.pendingName) {
        this.cb.clearPending(turn.pendingName);
        turn.pendingName = null;
      }
    }
  }

  /** Turn epilogue: promote a lost tail if the closing pass yielded nothing,
   *  clear pending, register for late renames. */
  private closeOut(turn: Turn): void {
    if (turn.seq === 0 && turn.allConfirmed.length === 0 && turn.pendingTail.length > 0) {
      // Closing pass produced nothing but drafts existed — never lose a turn.
      const name = this.resolveName(turn);
      const promoted = turn.pendingTail.map((s, i) => ({ ...s, segmentId: `turn:${turn.turnId}:${i}` }));
      this.cb.publish(name, promoted);
      turn.allConfirmed.push(...promoted);
      this.log(`[ChunkedTranscriber] turn ${turn.turnId}: promoted ${promoted.length} draft segment(s) on close`);
    }
    if (turn.pendingName) this.cb.clearPending(turn.pendingName);
    if (turn.allConfirmed.length > 0) {
      const winner = this.binder.bestOverlapName({ tStartMs: turn.t0, tEndMs: turn.t1 });
      if (!winner) {
        this.unresolved.push({ speaker: turn.clusterId, t0: turn.t0, t1: turn.t1, segments: turn.allConfirmed });
        if (this.unresolved.length > MAX_UNRESOLVED) this.unresolved.shift();
      }
    }
  }

  private resolveName(turn: Turn): string {
    const winner = this.binder.bestOverlapName({ tStartMs: turn.t0, tEndMs: turn.t1 });
    return winner?.name || turn.clusterId;
  }

  /** The bot's production quality gates. Returns whisper segments or null. */
  private applyGates(result: TranscriptionResult, windowMs: number): TranscriptionResult['segments'] | null {
    if (!result.text || !result.text.trim()) return null;
    const prob = result.language_probability ?? 0;
    if (!this.cb.language && prob > 0 && prob < 0.3) return null;
    const seg0 = result.segments?.[0];
    if (seg0) {
      const noSpeech = seg0.no_speech_prob ?? 0;
      const logProb = seg0.avg_logprob ?? 0;
      const compression = seg0.compression_ratio ?? 1;
      const duration = (seg0.end || 0) - (seg0.start || 0);
      if ((noSpeech > 0.5 && logProb < -0.7) || (logProb < -0.8 && duration < 2.0) || compression > 2.4) return null;
    }
    // Phrase-list hallucination filter ONLY on short windows: that's where
    // whisper invents "Thank you."-class junk. On longer RMS-vouched real
    // speech the same phrases ("Yes.") are legitimate answers.
    if (windowMs < 2000 && isHallucination(result.text)) {
      this.log(`[ChunkedTranscriber] [FILTERED] "${result.text.substring(0, 60)}"`);
      return null;
    }
    return (result.segments && result.segments.length > 0)
      ? result.segments
      : [{ text: result.text, start: 0, end: 0 } as any];
  }
}
