/**
 * ChunkedTranscriber — THE single-channel transcription core for the MIXED lane
 * (in-tab extension, bot tab-audio, Zoom, MS Teams). One mixed audio stream in;
 * named transcript segments out.
 *
 *   PCM frames ──► RING BUFFER (passive, audio-time indexed)
 *      │              never submitted, never trimmed mid-flight
 *      │
 *      └──► PyannoteSegmenter (pyannote-segmentation-3.0) — the ONLY cut signal
 *               │  boundary {tMs, kind} at ~13ms frame resolution; the cut is
 *               │  applied RETROACTIVELY to an exact span from the ring. No
 *               │  embedding/clustering on the cut path → early, never slides.
 *               ▼
 *           TURN — opened on speech start / speaker change, closed on speaker
 *               change / speech end / overlap edge / TURN_MAX_MS roll / dispose.
 *               ▼
 *           CONTINUOUS CONFIRMATION (LocalAgreement-2, shared @vexa/transcribe-
 *           buffer): every commit resubmits the turn's UNCONFIRMED window
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
 *   WHO (the namer): speaker = the max-overlap lit-hint over the turn span
 *        (ClusterNameBinder, hints-only — no diarization). The per-turn
 *        segmentation id is the key; a turn with no overlapping hint publishes
 *        provisionally under that id and is repainted in place (same segment_ids)
 *        when a later hint produces a window match. NO speaker clustering — the
 *        cut is segmentation, the name is hints.
 *
 * Prompt chaining: every submission carries the tail of the confirmed text
 * (across turns too) as initial_prompt.
 *
 * What is deliberately ABSENT: live mutable buffers, trim/in-flight
 * reconciliation, and any speaker-embedding/clustering. Audio→text is a pure
 * function of the ring span; ordering falls out of the strictly serialized queue.
 */

import { PyannoteSegmenter, type BoundaryEvent } from './pyannote-segmenter.js';
import { env as transformersEnv } from '@huggingface/transformers';
import { ClusterNameBinder, type HintKind } from './cluster-name-binder.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import { localAgreement } from '@vexa/transcribe-buffer';

const SAMPLE_RATE = 16000;
/** Near-silent spans are dropped before Whisper (desktop's DROP_RMS). */
const DROP_RMS = 0.006;
/** Ring capacity — must hold a full unconfirmed window plus segmenter lag. */
const RING_MS = 120_000;
/** Cap on the prompt fed to the next call (Whisper prompt window is small). */
const PROMPT_TAIL_CHARS = 200;
/** Unresolved turns kept for late hint renames. */
const MAX_UNRESOLVED = 100;
/** Late-box claim window: the active-speaker box lights up AFTER speech starts, so a
 *  still-unnamed (provisional) turn whose end falls within this window before a fresh
 *  hint is that speaker's — claim it. Wider than the binder's match tolerance (2.5s):
 *  its job is the warm-up/restart backward-reach (name the opening seg_N turns once the
 *  first hint lands), which the symmetric window-match can't reach. Bounded so it stays
 *  within the recent gap and never sweeps an older, different speaker's region. */
const CLAIM_WINDOW_MS = 8000;
/** A real silence at least this long resets the in-memory Whisper prompt: a fresh
 *  utterance shouldn't inherit the prior context, and any silence-hallucination
 *  poison ("Продолжение следует") is cleared before it can feed back and loop.
 *  Above natural sentence/breath pauses (<1.5s) so continuous speech keeps its
 *  context; just past a normal end-of-utterance gap (~2.5s) so it fires on a
 *  genuine break between speakers. */
const SILENCE_PROMPT_RESET_MS = 3000;
/** Cap on the UNCONFIRMED window — if stability stalls this long, the open turn
 *  force-rolls into a fresh turn (everything confirms). Inside Whisper's input. */
const TURN_MAX_MS = 28_000;
/** TTL idle-finalize: if the open turn has unconfirmed pending and no VOICED update
 *  has arrived for this long (speaker paused / segmenter didn't fire a close on
 *  continuous live-mixed audio), commit the pending now instead of waiting. Pairs
 *  with the stricter 3-pass agreement so it never strands text. Above natural
 *  sentence pauses, below an awkward wait. */
const CONFIRM_TTL_MS = 2500;
/** Don't bother Whisper with unconfirmed windows shorter than this unless
 *  the turn is closing. */
const MIN_SUBMIT_MS = 800;
/** Time-based resubmission cadence for the OPEN turn (the bot's
 *  submitInterval): pending refreshes and LocalAgreement stability build at
 *  this pace instead of waiting for the next boundary (which can be 10s away
 *  inside a monologue). Pending ≈ tick + RTT; confirm ≈ 2 ticks. */
const SUBMIT_TICK_MS = 2000;

export interface ChunkSegment {
  text: string;
  /** Audio-time ms (same timebase as feedAudio's tsMs). */
  startMs: number;
  endMs: number;
  language: string;
  /** Stable suffix — host prefixes its session uid. Same id on rename. */
  segmentId: string;
}

/** The cut source: streams frames, emits boundaries via the sink set at
 *  construction. PyannoteSegmenter in production; injectable for tests. */
export interface BoundarySource {
  appendFrame(pcm: Float32Array, tsMs: number): Promise<unknown>;
  reset(): void;
}

export interface ChunkedTranscriberCallbacks {
  /** One Whisper round-trip. Called strictly serially. */
  transcribe: (pcm: Float32Array, prompt?: string) => Promise<TranscriptionResult>;
  /** ONE atomic bundle: newly confirmed segments (persisted) + the speaker's
   *  surviving pending tail (full replace). They MUST travel together — a
   *  confirm published with empty pending deletes the client's draft block
   *  and the text visibly vanishes until the next submission. */
  publish: (speaker: string, confirmed: ChunkSegment[], pending: ChunkSegment[]) => void;
  /** Pending-only refresh of the open turn (nothing confirmed this pass). */
  publishPending: (speaker: string, segments: ChunkSegment[]) => void;
  /** Drop a speaker's pending drafts (turn moved to another name / closed). */
  clearPending: (speaker: string) => void;
  /** Late hint evidence renamed a provisionally-labeled turn: republish the
   *  SAME segment ids under the new name (and clear the old name's pending). */
  rename: (oldSpeaker: string, newSpeaker: string, segments: ChunkSegment[]) => void;
  /** Explicit language (skips Whisper's language-probability gate). */
  language?: string;
  /** Override the cut source (default: PyannoteSegmenter). The factory receives
   *  the boundary sink; the source calls it to cut. Test / advanced seam. */
  makeSegmenter?: (onBoundary: (ev: BoundaryEvent) => void) => Promise<BoundarySource>;
  log?: (msg: string) => void;
  /** Surface a transcribe FAILURE (P18: fail loud + attributable). The turn still
   *  degrades gracefully, but the host gets the fault to make it observable instead of
   *  a silent "no transcript". Receives the thrown value (e.g. a TranscriptionError). */
  onError?: (fault: unknown) => void;
}

interface RingFrame { pcm: Float32Array; tMs: number }
/** Segmentation lifecycle items on the serialized queue: a boundary opens a turn
 *  (speech start / speaker change) or closes the open one (speaker change / end). */
type SegItem =
  | { kind: 'open'; t0: number; segId: string }
  | { kind: 'close'; t1: number };
interface Turn {
  /** The per-turn segmentation id — the namer's key (no clustering). */
  clusterId: string;
  turnId: number;
  t0: number;
  /** Latest committed end (audio ms). */
  t1: number;
  /** Audio confirmed & published up to here. */
  confirmedUpToMs: number;
  /** Recent submissions' words (LocalAgreement-N, newest first). Reset on confirm. */
  history: string[][];
  /** Confirmed-segment counter → stable ids turn:{turnId}:{seq}. */
  seq: number;
  /** Live edge of the last submission — ticks skip when no new audio. */
  lastSubmitEndMs: number;
  /** Wall-clock of the last VOICED submission (text produced). The TTL idle-finalize
   *  commits the pending if no voiced update arrives within CONFIRM_TTL_MS. */
  lastVoicedWallMs: number;
  /** Everything confirmed in this turn — for late hint renames. */
  allConfirmed: ChunkSegment[];
  /** Name the pending tail was last published under. */
  pendingName: string | null;
  /** Last unconfirmed tail — promoted if the closing pass returns nothing. */
  pendingTail: ChunkSegment[];
  /** Sticky speaker: set once this turn resolves to a REAL name. While null the turn
   *  is UNATTRIBUTED and stays eligible for (re)attribution/claim/priority; once set,
   *  the name is locked so later hints (incl. brief "hmm" flickers) can't flip the
   *  turn's pending. Priority is for the unattributed, never for already-attributed. */
  resolvedName: string | null;
}
/** A committed turn whose hint hasn't arrived yet (provisional segmentation id) —
 *  re-resolved when a later hint produces a window match. Segments live in
 *  clusterSegments, so only the window + key are kept here. */
interface UnresolvedTurn { clusterId: string; t0: number; t1: number }

function rms(s: Float32Array): number {
  if (s.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < s.length; i++) sum += s[i] * s[i];
  return Math.sqrt(sum / s.length);
}

export class ChunkedTranscriber {
  private segmenter: BoundarySource | null = null;
  private segCounter = 0;
  private readonly binder = new ClusterNameBinder({});
  private readonly log: (msg: string) => void;

  private ring: RingFrame[] = [];
  private ringMs = 0;

  /** First audio frame's timestamp — the FIRST turn back-extends to here
   *  (bounded): the model needs seconds to lock on, but speech from t=0 is
   *  already in the ring. Without this the session opens with a hole. */
  private firstAudioMs: number | null = null;
  private firstTurnSeen = false;

  /** Serialized work queue: turn-lifecycle items and time-tick resubmissions. */
  private queue: Array<SegItem | 'tick'> = [];
  /** Timestamp of the freshest audio in the ring (the live edge). */
  private latestAudioMs = 0;
  private pumping = false;
  private lastConfirmedText = '';
  /** Audio-time end of the last processed commit — used to detect the silence
   *  gap that resets the prompt (SILENCE_PROMPT_RESET_MS). */
  private lastAudioEndMs = 0;
  private commitCounter = 0;
  private turnCounter = 0;
  private disposed = false;

  /** The open turn. */
  private turn: Turn | null = null;
  private idleTimer: ReturnType<typeof setInterval> | null = null;

  /** Turns published under a provisional segmentation id, awaiting hint evidence. */
  private unresolved: UnresolvedTurn[] = [];

  /** Every CONFIRMED segment published per key — the binder's continuous
   *  re-resolve repaints these (rename) when a key's name changes. */
  private clusterSegments = new Map<string, ChunkSegment[]>();
  /** Name each key's segments were last published under (segmentation id until resolved). */
  private clusterName = new Map<string, string>();

  /** High-water mark of CONFIRMED audio. Flicker can open a new turn inside
   *  audio the previous turn already confirmed — without this clamp the same
   *  sentences publish twice (identical timestamps, different turns). */
  private confirmedHighWaterMs = 0;

  private constructor(private readonly cb: ChunkedTranscriberCallbacks) {
    this.log = cb.log || (() => { /* silent */ });
  }

  static async create(cb: ChunkedTranscriberCallbacks): Promise<ChunkedTranscriber> {
    const t = new ChunkedTranscriber(cb);
    // Continuous re-resolve: when a key's voted name changes (hysteresis-
    // cleared), repaint that key's pending + published segments live.
    t.binder.onLateResolve = (clusterId, name) => t.onClusterRename(clusterId, name);
    transformersEnv.allowLocalModels = true;
    transformersEnv.allowRemoteModels = true; // first run downloads from HF; cached after
    // Offline bake: point the HF cache at the model dir baked into the image
    // (Dockerfile warms /opt/hf-cache at build time) so the segmenter loads
    // pyannote from disk at runtime — no live-meeting HF download stall.
    if (process.env.VEXA_HF_CACHE) transformersEnv.cacheDir = process.env.VEXA_HF_CACHE;
    // Segmentation OWNS the cut — pyannote boundaries are the only cut signal.
    const makeSegmenter = cb.makeSegmenter
      ?? ((onBoundary) => PyannoteSegmenter.create({ inferIntervalMs: 500, onBoundary }));
    t.segmenter = await makeSegmenter((ev) => t.handleBoundary(ev));
    // One 1s heartbeat drives:
    //  - TICK: resubmit the open turn up to the live audio edge on a fixed
    //    cadence (latency decoupled from boundary timing),
    //  - ROLL: bound the unconfirmed Whisper window — an over-long open turn
    //    (continuous speech, stalled stability) force-rolls into a fresh turn.
    t.idleTimer = setInterval(() => {
      if (t.pumping) return;
      // Liveness: an item enqueued during the pump's closing pass misses the
      // drain loop; with no further boundaries nothing would re-pump it.
      if (t.queue.length > 0) { void t.pump(); return; }
      if (!t.turn) return;
      if (t.latestAudioMs - t.turn.confirmedUpToMs > TURN_MAX_MS) {
        t.queue.push({ kind: 'close', t1: t.latestAudioMs });
        t.queue.push({ kind: 'open', t0: t.latestAudioMs, segId: `seg_${t.segCounter++}` });
        void t.pump();
        return;
      }
      // TTL idle-finalize: pending exists but no voiced update for CONFIRM_TTL_MS
      // (speaker paused / segmenter didn't close on continuous live audio) → commit
      // what we have by closing the turn, instead of holding it for the 3rd pass.
      if (t.turn.pendingTail.length > 0 && Date.now() - t.turn.lastVoicedWallMs > CONFIRM_TTL_MS) {
        t.queue.push({ kind: 'close', t1: t.latestAudioMs });
        void t.pump();
        return;
      }
      if (t.latestAudioMs - Math.max(t.turn.lastSubmitEndMs, t.turn.confirmedUpToMs) >= SUBMIT_TICK_MS) {
        t.queue.push('tick');
        void t.pump();
      }
    }, 1000);
    t.log(`[ChunkedTranscriber] ready (segmentation-cut turns, hints-only naming, LocalAgreement-3 confirmation + TTL finalize)`);
    return t;
  }

  /** One mixed-audio frame. Ring + segmentation model — nothing else. */
  feedAudio(pcm: Float32Array, tsMs: number): void {
    if (this.disposed) return;
    if (this.firstAudioMs === null) this.firstAudioMs = tsMs;
    this.latestAudioMs = Math.max(this.latestAudioMs, tsMs + (pcm.length / SAMPLE_RATE) * 1000);
    this.ring.push({ pcm, tMs: tsMs });
    this.ringMs += (pcm.length / SAMPLE_RATE) * 1000;
    while (this.ring.length > 0 && this.ringMs > RING_MS) {
      const f = this.ring.shift()!;
      this.ringMs -= (f.pcm.length / SAMPLE_RATE) * 1000;
    }
    this.segmenter?.appendFrame(pcm, tsMs).catch((e: any) =>
      this.log(`[ChunkedTranscriber] segmenter error: ${e?.message}`));
  }

  /** Timestamped platform hint ("who's lit"). Also re-resolves turns that
   *  published provisionally — overlap evidence only, never inheritance. */
  recordHint(name: string, kind: HintKind, tMs: number, isEnd = false): void {
    this.binder.recordHint({ name, tMs, kind, isEnd });
    if (!name) return;
    // Faster attribution of an UNATTRIBUTED open turn: a just-arrived hint may now
    // name it — resolve immediately so its pending repaints under the right speaker
    // instead of showing seg_N until the next tick. STICKY: only while unattributed
    // (resolvedName == null); once attributed we never re-resolve the open turn, so a
    // brief flicker hint can't flip an already-correct pending. Priority for the
    // unattributed, NOT for pending that's already attributed.
    if (this.turn && !this.turn.resolvedName && (this.turn.pendingTail.length > 0 || this.turn.allConfirmed.length > 0)) {
      this.resolveName(this.turn);
    }
    if (this.unresolved.length === 0) return;
    // Two passes for turns that committed before their hint arrived:
    //  1. window-match — a hint whose lag-shifted window overlaps the turn names it
    //     (re-resolve casts the vote → onClusterRename repaints). Preferred.
    //  2. late-box claim — if STILL unnamed and the turn ended within CLAIM_WINDOW_MS
    //     before this hint, it's the gap the late active-speaker box left, so it's
    //     THIS speaker's: claim it. Bounded so it can't reach a prior speaker's tail;
    //     only fills provisional gaps, never overwrites a resolved name. (Skip on isEnd.)
    const claimFrom = isEnd ? Infinity : tMs - CLAIM_WINDOW_MS;
    const still: UnresolvedTurn[] = [];
    for (const u of this.unresolved) {
      const r = this.binder.resolve({ clusterId: u.clusterId, tStartMs: u.t0, tEndMs: u.t1 });
      if (r.source !== 'provisional-cluster-id') continue;        // window-matched → repainted, drop
      if (u.t1 >= claimFrom) this.claimTurn(u.clusterId, name);   // late-box gap → claim for this speaker
      else still.push(u);
    }
    this.unresolved = still.slice(-MAX_UNRESOLVED);
  }

  /** Late-box claim: repaint a provisional turn's published segments under `name`
   *  (the speaker whose box just lit). Same path as a binder rename. */
  private claimTurn(clusterId: string, name: string): void {
    const old = this.clusterName.get(clusterId) ?? clusterId;
    if (old === name) return;
    this.clusterName.set(clusterId, name);
    const segs = this.clusterSegments.get(clusterId);
    if (segs && segs.length) {
      this.cb.rename(old, name, segs);
      this.log(`[ChunkedTranscriber] late-box claim ${clusterId} → "${name}" (${segs.length} segment(s))`);
    }
  }

  stats(): { commits: number; turns: number; queued: number; unresolved: number; binder: ReturnType<ClusterNameBinder['stats']> } {
    return { commits: this.commitCounter, turns: this.turnCounter, queued: this.queue.length, unresolved: this.unresolved.length, binder: this.binder.stats() };
  }

  /** Session end: flush the carried span and close the open turn. Resolves
   *  AFTER the final turn has published — callers that publish session_end
   *  (the bot's graceful leave) must await it or the closing words are lost. */
  async dispose(): Promise<void> {
    if (this.disposed) return;
    this.disposed = true;
    if (this.idleTimer) { clearInterval(this.idleTimer); this.idleTimer = null; }
    // An in-flight pump owns the queue — wait it out, then run the closing
    // pass (pump(true) returns immediately while another pump holds the lock).
    while (this.pumping) await new Promise(r => setTimeout(r, 50));
    await this.pump(true);
    while (this.pumping) await new Promise(r => setTimeout(r, 50));
    try { this.segmenter?.reset(); } catch { /* best effort */ }
  }

  // ── Cutting (segmentation boundaries open/close turns) ──────────

  /** Pyannote boundary = the ONLY cut signal. A boundary opens a turn (speech
   *  start / speaker change) and/or closes the open one (speaker change / end /
   *  overlap edge). Lifecycle items go through the serialized queue so turn
   *  state stays single-threaded under the pump lock. */
  private handleBoundary(ev: BoundaryEvent): void {
    if (this.disposed) return;
    switch (ev.kind) {
      case 'silence→speaker':
        this.openTurn(ev.tMs);
        break;
      case 'speaker→speaker':
      case 'overlap-onset':
      case 'overlap-offset':
        this.closeTurn(ev.tMs);   // hard-split at the change / overlap edge
        this.openTurn(ev.tMs);
        break;
      case 'speaker→silence':
        this.closeTurn(ev.tMs);
        break;
    }
  }

  private openTurn(t0: number): void {
    // Cold start: the model needs seconds of audio to lock on, so its first
    // boundary lands well after capture began — but speech from frame one is in
    // the ring. Back-extend the FIRST turn to the first audio frame (bounded;
    // leading silence is harmless to Whisper and the RMS gate still protects a
    // truly silent prefix-heavy span).
    if (!this.firstTurnSeen) {
      this.firstTurnSeen = true;
      if (this.firstAudioMs !== null && t0 - this.firstAudioMs > 0 && t0 - this.firstAudioMs <= 12_000) {
        this.log(`[ChunkedTranscriber] first turn back-extended ${t0 - this.firstAudioMs}ms to session start`);
        t0 = this.firstAudioMs;
      }
    }
    this.queue.push({ kind: 'open', t0, segId: `seg_${this.segCounter++}` });
    void this.pump();
  }

  private closeTurn(t1: number): void {
    this.queue.push({ kind: 'close', t1 });
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
        const item = this.queue.shift()!;
        try {
          if (item !== 'tick' && item.kind === 'open') {
            await this.openTurnApply(item);
          } else if (item !== 'tick' && item.kind === 'close') {
            await this.closeTurnApply(item);   // submits + closes the open turn
            continue;
          }
          // COALESCE: with a backlog, every tick would submit the same
          // [confirmedUpTo..liveEdge] window — N identical, ever-larger Whisper
          // calls. Apply state for all; submit once per drain batch, unless the
          // next item opens/closes a turn (let it apply first).
          const next = this.queue[0];
          const moreTurnWork = next !== undefined && next !== 'tick';
          if (!moreTurnWork && this.turn) await this.submitTurn(this.turn, false);
        } catch (e: any) {
          const tag = item === 'tick' ? 'tick' : item.kind;
          this.log(`[ChunkedTranscriber] ${tag} failed: ${e?.message}`);
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
    // Items enqueued during the closing pass (after the drain loop exited)
    // would otherwise strand until the next boundary.
    if (this.queue.length > 0) void this.pump(closeAtEnd);
  }

  /** Open a new segmentation turn. Closes any still-open turn first (defensive —
   *  a 'close' normally precedes, but flicker can skip it). Does NOT submit —
   *  the pump submits the open turn once per drain batch (and ticks resubmit). */
  private async openTurnApply(item: { t0: number; segId: string }): Promise<void> {
    this.commitCounter++;
    if (this.turn) { const prev = this.turn; this.turn = null; await this.submitTurn(prev, true); }
    const t0 = Math.max(item.t0, this.confirmedHighWaterMs);
    // Real silence since the last turn → reset the in-memory prompt so the new
    // utterance starts clean (no inherited context, no silence-hallucination loop).
    if (this.lastAudioEndMs > 0 && t0 - this.lastAudioEndMs >= SILENCE_PROMPT_RESET_MS) {
      this.lastConfirmedText = '';
    }
    this.turn = {
      clusterId: item.segId, turnId: this.turnCounter++,
      t0, t1: t0, confirmedUpToMs: t0,
      history: [], seq: 0, lastSubmitEndMs: 0, allConfirmed: [], pendingName: null, pendingTail: [],
      lastVoicedWallMs: Date.now(), resolvedName: null,
    };
  }

  /** Close the open turn at a boundary: everything confirms (last chance). */
  private async closeTurnApply(item: { t1: number }): Promise<void> {
    if (!this.turn) return;
    const t = this.turn;
    // Clamp the boundary to available audio and never below confirmed.
    t.t1 = Math.max(t.confirmedUpToMs, Math.min(item.t1, this.latestAudioMs || item.t1));
    this.lastAudioEndMs = Math.max(this.lastAudioEndMs, t.t1);
    this.turn = null;
    await this.submitTurn(t, true);
  }

  /** One submission of the turn's unconfirmed window. While the turn is open,
   *  confirmation is LocalAgreement-2 (the bot's word-prefix stability); on
   *  close everything confirms. */
  private async submitTurn(turn: Turn, closing: boolean): Promise<void> {
    const spanStart = turn.confirmedUpToMs;
    // Open turns read to the LIVE AUDIO EDGE, not the last commit — pending
    // tracks speech in near-real-time and stability builds at tick cadence.
    // The trailing un-committed second may belong to the next speaker; the
    // LocalAgreement tail guard (the last forming words never confirm) keeps
    // that bleed out of confirmed output until segmentation rules on it.
    // Closing turns read exactly to the committed boundary.
    const spanEnd = closing ? turn.t1 : Math.max(turn.t1, this.latestAudioMs || turn.t1);
    if (spanEnd - spanStart < (closing ? 250 : MIN_SUBMIT_MS)) {
      if (closing) await this.closeOut(turn);
      return;
    }
    // No new audio since the last pass — an identical window returns
    // identical text and confirms nothing; don't waste the call.
    if (!closing && spanEnd - turn.lastSubmitEndMs < 500) return;
    turn.lastSubmitEndMs = spanEnd;

    const pcm = this.cut(spanStart, spanEnd);
    if (pcm.length < SAMPLE_RATE * 0.2 || rms(pcm) < DROP_RMS) {
      if (closing) await this.closeOut(turn);
      return;
    }

    const prompt = this.lastConfirmedText ? this.lastConfirmedText.slice(-PROMPT_TAIL_CHARS) : undefined;
    let result: TranscriptionResult | null = null;
    try {
      result = await this.cb.transcribe(pcm, prompt);
    } catch (e: any) {
      this.cb.onError?.(e);                                            // P18: surface the fault…
      this.log(`[ChunkedTranscriber] transcribe failed: ${e?.message}`);   // …keep the local log too
    }
    const gated = result ? this.applyGates(result, spanEnd - spanStart) : null;
    if (!gated || gated.length === 0) {
      if (closing) await this.closeOut(turn);
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
      if (closing) await this.closeOut(turn);
      return;
    }
    turn.lastVoicedWallMs = Date.now();   // voiced update arrived — resets the TTL idle-finalize

    // LocalAgreement-N (shared confirm core, @vexa/transcribe-buffer): confirm whole
    // leading segments whose words are stable across N (default 3) consecutive
    // submissions; the still-forming tail stays pending. On close everything confirms.
    const agreement = localAgreement(mapped, turn.history, spanEnd, closing);
    const confirmCount = agreement.confirmCount;
    turn.history = agreement.history;

    const name = this.resolveName(turn);
    if (turn.pendingName && turn.pendingName !== name) this.cb.clearPending(turn.pendingName);

    const tail: ChunkSegment[] = mapped.slice(confirmCount).map((s, i) => ({
      text: s.text, startMs: s.startMs, endMs: s.endMs, language: s.language,
      segmentId: `turn:${turn.turnId}:p${i}`,
    }));

    if (confirmCount > 0) {
      const confirmed: ChunkSegment[] = mapped.slice(0, confirmCount).map(s => ({
        text: s.text, startMs: s.startMs, endMs: s.endMs, language: s.language,
        segmentId: `turn:${turn.turnId}:${turn.seq++}`,
      }));
      // ONE bundle: confirmed + surviving tail. Splitting them deletes the
      // client's pending block for seconds (the "vanishing transcript" bug).
      this.cb.publish(name, confirmed, closing ? [] : tail);
      turn.allConfirmed.push(...confirmed);
      // Track per-key so a later name change repaints these in place.
      let cs = this.clusterSegments.get(turn.clusterId);
      if (!cs) { cs = []; this.clusterSegments.set(turn.clusterId, cs); }
      cs.push(...confirmed);
      this.clusterName.set(turn.clusterId, name);
      turn.confirmedUpToMs = spanStart + mapped[confirmCount - 1].relEnd * 1000;
      this.confirmedHighWaterMs = Math.max(this.confirmedHighWaterMs, turn.confirmedUpToMs);
      const txt = confirmed.map(s => s.text).join(' ');
      this.lastConfirmedText = (this.lastConfirmedText + ' ' + txt).slice(-PROMPT_TAIL_CHARS * 2);
      turn.pendingName = !closing && tail.length > 0 ? name : null;
      turn.pendingTail = closing ? [] : tail;
    } else if (!closing) {
      turn.pendingTail = tail;
      if (tail.length > 0) {
        turn.pendingName = name;
        this.cb.publishPending(name, tail);
      } else if (turn.pendingName) {
        this.cb.clearPending(turn.pendingName);
        turn.pendingName = null;
      }
    }

    if (closing) {
      await this.closeOut(turn);
    }
  }

  /** Turn epilogue: promote a lost tail if the closing pass yielded nothing,
   *  clear pending, register for late hint renames. */
  private async closeOut(turn: Turn): Promise<void> {
    // The whole turn span is adjudicated once closed — later commits
    // (overlap duplicates) must not re-transcribe any of it.
    this.confirmedHighWaterMs = Math.max(this.confirmedHighWaterMs, turn.t1, turn.confirmedUpToMs);
    if (turn.seq === 0 && turn.allConfirmed.length === 0 && turn.pendingTail.length > 0) {
      // Closing pass produced nothing but drafts existed — never lose a turn.
      const name = this.resolveName(turn);
      const promoted = turn.pendingTail.map((s, i) => ({ ...s, segmentId: `turn:${turn.turnId}:${i}` }));
      this.cb.publish(name, promoted, []);
      turn.allConfirmed.push(...promoted);
      let cs = this.clusterSegments.get(turn.clusterId);
      if (!cs) { cs = []; this.clusterSegments.set(turn.clusterId, cs); }
      cs.push(...promoted);
      this.clusterName.set(turn.clusterId, name);
      // Drafts come from LIVE-EDGE submissions and can extend past the
      // committed boundary — the high-water mark must cover everything
      // PUBLISHED, or the next turn re-transcribes the promoted audio and
      // the same sentence appears under two turns.
      this.confirmedHighWaterMs = Math.max(this.confirmedHighWaterMs, promoted[promoted.length - 1].endMs);
      this.log(`[ChunkedTranscriber] turn ${turn.turnId}: promoted ${promoted.length} draft segment(s) on close`);
    }
    if (turn.pendingName) this.cb.clearPending(turn.pendingName);
    // Register a name vote for the closed turn. If no hint overlaps yet
    // (provisional), queue it for re-resolve when a later hint arrives.
    if (turn.allConfirmed.length > 0) {
      const r = this.binder.resolve({ clusterId: turn.clusterId, tStartMs: turn.t0, tEndMs: turn.t1 });
      if (r.source === 'provisional-cluster-id') {
        this.unresolved.push({ clusterId: turn.clusterId, t0: turn.t0, t1: turn.t1 });
        if (this.unresolved.length > MAX_UNRESOLVED) this.unresolved.shift();
      }
    }
  }

  private resolveName(turn: Turn): string {
    // STICKY ATTRIBUTION. Once a turn has resolved to a real speaker, lock it: later
    // hints (a brief "hmm" box-flicker, the other speaker's lag-shifted overlap) must
    // NOT flip an already-attributed turn's pending. Priority/claim is for the
    // UNATTRIBUTED, never for the attributed. While unattributed we keep resolving:
    // window-match (lag-corrected overlap) casts the per-key vote; the first REAL
    // result locks the name (and onLateResolve → onClusterRename paints it in).
    if (turn.resolvedName) return turn.resolvedName;
    const r = this.binder.resolve({ clusterId: turn.clusterId, tStartMs: turn.t0, tEndMs: turn.t1 });
    if (r.source !== 'provisional-cluster-id') turn.resolvedName = r.speakerName;
    return r.speakerName;
  }

  /** Binder says this key's name changed → repaint its published segments
   *  (rename) and its live pending tail. Stable segment ids let the client
   *  update in place (no segment is keyed by speaker name). */
  private onClusterRename(clusterId: string, name: string): void {
    const old = this.clusterName.get(clusterId) ?? clusterId;
    this.clusterName.set(clusterId, name);
    const segs = this.clusterSegments.get(clusterId);
    if (segs && segs.length && old !== name) {
      this.cb.rename(old, name, segs);
      this.log(`[ChunkedTranscriber] ${clusterId} → "${name}" (repainted ${segs.length} segment(s))`);
    }
    // Repaint the live pending tail if the open turn belongs to this key.
    const turn = this.turn;
    if (turn && turn.clusterId === clusterId && turn.pendingTail.length > 0) {
      if (turn.pendingName && turn.pendingName !== name) this.cb.clearPending(turn.pendingName);
      turn.pendingName = name;
      this.cb.publishPending(name, turn.pendingTail);
    }
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
    // NO phrase-list hallucination filter here — live monitoring showed it
    // killing real interview answers ("Yes.", "Okay.", "Right?", "Thank
    // you.") even on short windows. The hallucination vector it was built
    // for (whisper inventing phrases on silence) is closed upstream: spans
    // are model-cut SPEECH regions and RMS-gated, and the no_speech/logprob/
    // compression gates above catch acoustic junk. Prompt-echo is filtered
    // separately at the segment level.
    return (result.segments && result.segments.length > 0)
      ? result.segments
      : [{ text: result.text, start: 0, end: 0 } as any];
  }
}
