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
 *               COVERAGE IS CONTIGUOUS: a turn begins where the last one ended,
 *               so a boundary the model never emits (its stream has multi-second
 *               recall gaps over continuous speech) delays words, never deletes
 *               them. The cut says WHERE the speaker changed; it does not get to
 *               say which audio exists.
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
/** Pyannote's speech-end frame can land a little early. On a clean
 *  speaker→silence close, send a small trailing context pad to STT so final
 *  phones/words survive, while clipping published timestamps to the committed
 *  speech boundary. Speaker→speaker cuts get no pad: attribution wins there. */
const SILENCE_CLOSE_CONTEXT_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_SILENCE_CLOSE_CONTEXT_MS) || 350);
/** A speech-end close immediately after another accepted cut is boundary churn,
 * not enough evidence to end the new turn. Real speaker changes and overlap
 * edges remain hard cuts regardless of their spacing. */
const EARLY_SILENCE_CLOSE_MS = 2000;
/** TTL idle-finalize: if the open turn has unconfirmed pending and no VOICED update
 *  has arrived for this long (speaker paused / segmenter didn't fire a close on
 *  continuous live-mixed audio), commit the pending now instead of waiting. Pairs
 *  with the stricter 3-pass agreement so it never strands text. Above natural
 *  sentence pauses, below an awkward wait. */
const CONFIRM_TTL_MS = 2500;
/** Don't bother Whisper with unconfirmed windows shorter than this unless
 *  the turn is closing. */
const MIN_SUBMIT_MS = 800;
/** How far a newly-opened turn may reach back to pick up audio no turn covered (see
 *  openTurnApply). Bounds what a genuinely long silence can hand Whisper in one call — its
 *  input window is 30s, and a span past that is truncated with its timestamps still claiming
 *  the whole reach. */
const COVERAGE_BACKFILL_MAX_MS = 20_000;
/** Time-based resubmission cadence for the OPEN turn (the bot's
 *  submitInterval): pending refreshes and LocalAgreement stability build at
 *  this pace instead of waiting for the next boundary (which can be 10s away
 *  inside a monologue). Pending ≈ tick + RTT; confirm ≈ 2 ticks. */
const SUBMIT_TICK_MS = 2000;
/** A short isolated active-speaker UI switch (Zoom/Teams) right after a different
 *  published speaker is held provisional rather than stamped — without acoustic
 *  evidence a brief tile flip is more likely a stale/echoed hint than a real,
 *  sub-{MAX}ms turn by someone new. Tunable via VEXA_SHORT_UI_SWITCH_*. */
const SHORT_UI_SWITCH_MAX_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_SHORT_UI_SWITCH_MAX_MS) || 3200);
const SHORT_UI_SWITCH_GAP_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_SHORT_UI_SWITCH_GAP_MS) || 2500);

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
  /** Instantaneous per-hint outcome (the hint-hop instrument): 'matched' when the
   *  hint names/claims a turn at the moment it arrives (or re-asserts the open
   *  turn's already-resolved name); 'missed' when no turn overlaps it yet. A
   *  'missed' hint is still recorded in the binder and may window-match a later
   *  commit — this reports the hop's immediate fate, not the final binding. */
  onHintOutcome?: (o: { name: string; kind: HintKind; tMs: number; outcome: 'matched' | 'missed' }) => void;
}

interface RingFrame { pcm: Float32Array; tMs: number }

/** One contiguous run inside a cut: where it sits in the compressed audio, and the wall instant it
 *  came from. A cut over a gapless span has exactly one; each hole adds another. */
interface CutSpan { fromSample: number; wallStartMs: number; samples: number }
/** Segmentation lifecycle items on the serialized queue: a boundary opens a turn
 *  (speech start / speaker change) or closes the open one (speaker change / end). */
type SegItem =
  | { kind: 'open'; t0: number; segId: string }
  | { kind: 'close'; t1: number; contextPadMs?: number };
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
  /** Has this turn ever sent a real submission? Until it has, the first window is
   *  released as soon as MIN_SUBMIT_MS accrues (the first-submit fast path) instead
   *  of waiting a full SUBMIT_TICK_MS — a per-turn wait handover churn pays on EVERY
   *  turn, so it dominates the mixed lane's draft latency, not just the first turn. */
  firstSubmitDone: boolean;
  /** Wall-clock of the last VOICED submission (text produced). The TTL idle-finalize
   *  commits the pending if no voiced update arrives within CONFIRM_TTL_MS. */
  lastVoicedWallMs: number;
  /** Everything confirmed in this turn — for late hint renames. */
  allConfirmed: ChunkSegment[];
  /** Name the pending tail was last published under. */
  pendingName: string | null;
  /** Last unconfirmed tail — promoted if the closing pass returns nothing. */
  pendingTail: ChunkSegment[];
  /** Exclusive upper bound of the segment ids this turn has ever published under. A pass that
   *  segments into fewer pieces than the draft it replaces leaves the ids above it stranded in
   *  the consumer's store; this is what says which those are. */
  draftedUpToSeq: number;
  /** Sticky speaker: set once this turn resolves to a REAL name. While null the turn
   *  is UNATTRIBUTED and stays eligible for (re)attribution/claim/priority; once set,
   *  the name is locked so later hints (incl. brief "hmm" flickers) can't flip the
   *  turn's pending. Priority is for the unattributed, never for already-attributed. */
  resolvedName: string | null;
  /** Optional STT-only trailing context used on speech-end closes. Published
   *  timestamps and confirmed high-water still stop at t1. */
  contextEndMs?: number;
  /** Names this turn must NOT be (re)claimed to — a short-UI-switch hint that was
   *  held provisional, so a later claim/rename can't resurrect the bad name. */
  blockedNames?: Set<string>;
}
/** A committed turn whose hint hasn't arrived yet (provisional segmentation id) —
 *  re-resolved when a later hint produces a window match. Segments live in
 *  clusterSegments, so only the window + key are kept here. `reasserted` counts
 *  FRESH post-hold hints per blocked name: a transient tile flip never speaks
 *  again, so a blocked name that keeps re-asserting was the real speaker and
 *  earns its claim back (see recordHint). */
interface UnresolvedTurn { clusterId: string; t0: number; t1: number; blockedNames?: Set<string>; reasserted?: Map<string, number> }

/** Fresh hints of a held-back name needed to lift a short-UI-switch block. One
 *  heartbeat could be the tail of the same blip; two (~4s apart on the Zoom/Teams
 *  re-assert cadence) is a speaker who is still talking. */
const REASSERT_UNBLOCK_COUNT = 2;
/** How long after a held turn's end a blocked name's fresh hints still count as
 *  testimony about it. Two heartbeats plus slack; past this, the room has moved
 *  on and the hold stands. */
const REASSERT_WINDOW_MS = 8000;
/** A held turn is adjudicated by the SWEEP once its re-assertion window has
 *  passed: if the held-back name's total lit time around the turn reaches this,
 *  it was a speaker (heartbeats total seconds), not a tile flip (one short
 *  slice) — the vetoed match stands and the turn repaints. */
const REAL_SPEAKER_LIT_MS = 2000;
/** Audio-time cadence for the held-turn sweep. */
const SWEEP_EVERY_MS = 1000;

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
  private lastSweepMs = 0;
  private pumping = false;
  private lastConfirmedText = '';
  /** Audio-time end of the last processed commit — used to detect the silence
   *  gap that resets the prompt (SILENCE_PROMPT_RESET_MS). */
  private lastAudioEndMs = 0;
  private commitCounter = 0;
  private turnCounter = 0;
  private disposed = false;
  /** Boundary time of the prior cut accepted into the turn lifecycle. */
  private lastAcceptedBoundaryMs = -Infinity;
  /** An early close leaves the turn open. A prompt complementary onset is
   *  therefore a resume, not a second open/cut of that same turn. */
  private ignoredSilenceCloseMs: number | null = null;

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
  /** The last REAL speaker name published + where it ended — the short-UI-switch
   *  guard compares a fresh window-match against this to spot a brief tile flip. */
  private lastPublishedSpeaker: { name: string; endMs: number } | null = null;

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
      // FIRST-SUBMIT FAST PATH (port of the gmeet lane's #851 fix b). A turn that has
      // never submitted otherwise waits a whole SUBMIT_TICK_MS for its first window —
      // dead air before any draft, on top of the STT round-trip. Handover churn opens a
      // new turn constantly, so this wait is paid per turn, not once. Release the first
      // window the moment MIN_SUBMIT_MS of audio has accrued; the too-short / RMS gates in
      // submitTurn stay authoritative, so a near-silent opener is still skipped there.
      if (!t.turn.firstSubmitDone && t.latestAudioMs - t.turn.confirmedUpToMs >= MIN_SUBMIT_MS) {
        t.queue.push('tick');
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
    if (this.latestAudioMs - this.lastSweepMs >= SWEEP_EVERY_MS) {
      this.lastSweepMs = this.latestAudioMs;
      this.sweepHeld(this.latestAudioMs);
    }
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
    // Hint-hop instrument: did this hint find a turn RIGHT NOW? Emitted once per
    // start-hint at every exit below (end-hints close windows, they don't bind).
    let matchedNow = false;
    const report = (): void => {
      if (isEnd || !this.cb.onHintOutcome) return;
      this.cb.onHintOutcome({ name, kind, tMs, outcome: matchedNow ? 'matched' : 'missed' });
    };
    // Faster attribution of an UNATTRIBUTED open turn: a just-arrived hint may now
    // name it — resolve immediately so its pending repaints under the right speaker
    // instead of showing seg_N until the next tick. STICKY: only while unattributed
    // (resolvedName == null); once attributed we never re-resolve the open turn, so a
    // brief flicker hint can't flip an already-correct pending. Priority for the
    // unattributed, NOT for pending that's already attributed.
    if (this.turn && !this.turn.resolvedName && (this.turn.pendingTail.length > 0 || this.turn.allConfirmed.length > 0)) {
      this.resolveName(this.turn);
    }
    if (this.turn?.resolvedName === name) matchedNow = true;   // named (now or already) the open turn
    if (this.unresolved.length === 0) { report(); return; }
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
      const blocked = u.blockedNames ?? new Set<string>();
      const m = this.binder.matchWindow({ clusterId: u.clusterId, tStartMs: u.t0, tEndMs: u.t1 });
      if (!isEnd && blocked.has(name) && tMs - u.t1 <= REASSERT_WINDOW_MS) {
        // The held-back name is speaking AGAIN. The hold vetoed a window-match that
        // had already passed every binder gate, on suspicion of a tile flip — and a
        // transient flip never re-asserts, while a real speaker's heartbeat does.
        // Fresh testimony is judged on ARRIVAL TIME (within a couple of heartbeats
        // of the held turn's end), not on re-matching the past window, which a
        // later hint can never do. At the threshold the suspicion is disproved:
        // the original match stands — lift and claim.
        const seen = ((u.reasserted ??= new Map()).get(name) ?? 0) + 1;
        if (seen >= REASSERT_UNBLOCK_COUNT) {
          blocked.delete(name);
          this.log(`[ChunkedTranscriber] short-UI block lifted for "${name}" on ${u.clusterId} — re-asserted ×${seen}`);
          this.claimTurn(u.clusterId, name);
          matchedNow = true;
          continue;
        }
        u.reasserted.set(name, seen);
      }
      if (m && !blocked.has(m.name)) { this.claimTurn(u.clusterId, m.name); matchedNow = true; continue; }   // window-matched → repaint, drop
      if (u.t1 >= claimFrom && !blocked.has(name)) { this.claimTurn(u.clusterId, name); matchedNow = true; } // late-box gap → claim for this speaker
      else still.push(u);
    }
    this.unresolved = still.slice(-MAX_UNRESOLVED);
    report();
  }

  /** THE SWEEP (audio-clock driven, and once more at dispose): adjudicate every
   *  held or unnamed turn whose re-assertion window has closed, against the
   *  hints ALREADY recorded. A hold must not depend on a future hint — under
   *  hint-leads-turn interleaving there is none — and it must not be a life
   *  sentence: the binder match the hold vetoed either stands (the name shows
   *  real lit time around the turn) or the turn stays provisional. */
  private sweepHeld(nowMs: number): void {
    if (this.unresolved.length === 0) return;
    const still: UnresolvedTurn[] = [];
    for (const u of this.unresolved) {
      if (nowMs < u.t1 + REASSERT_WINDOW_MS) { still.push(u); continue; }
      const m = this.binder.matchWindow({ clusterId: u.clusterId, tStartMs: u.t0, tEndMs: u.t1 });
      if (!m) { still.push(u); continue; }
      const blocked = u.blockedNames;
      if (blocked?.has(m.name)) {
        const lit = this.binder.litMsAround(m.name, u.t0 - REASSERT_WINDOW_MS, u.t1 + REASSERT_WINDOW_MS);
        if (lit >= REAL_SPEAKER_LIT_MS) {
          blocked.delete(m.name);
          this.log(`[ChunkedTranscriber] sweep adjudicated ${u.clusterId} → "${m.name}" (lit ${Math.round(lit)}ms around the turn — a speaker, not a flip)`);
          this.claimTurn(u.clusterId, m.name);
          continue;
        }
        still.push(u);   // one short slice — the flip suspicion stands
        continue;
      }
      this.claimTurn(u.clusterId, m.name);   // ordinary late window-match, no hold involved
    }
    this.unresolved = still;
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
    // Final adjudication: every held turn gets its sweep verdict from the full
    // hint log before the session ends — a hold is never left undecided.
    this.sweepHeld(Infinity);
    try { this.segmenter?.reset(); } catch { /* best effort */ }
  }

  // ── Cutting (segmentation boundaries open/close turns) ──────────

  /** Pyannote boundary = the ONLY cut signal. A boundary opens a turn (speech
   *  start / speaker change) and/or closes the open one (speaker change / end /
   *  overlap edge). Lifecycle items go through the serialized queue so turn
   *  state stays single-threaded under the pump lock. */
  private handleBoundary(ev: BoundaryEvent): void {
    if (this.disposed) return;
    const sincePriorCutMs = ev.tMs - this.lastAcceptedBoundaryMs;
    if (ev.kind === 'speaker→silence' && sincePriorCutMs < EARLY_SILENCE_CLOSE_MS) {
      this.ignoredSilenceCloseMs = ev.tMs;
      this.log(`[boundary] ignore early speaker→silence at ${Math.round(ev.tMs)}ms `
        + `(${Math.round(sincePriorCutMs)}ms after prior cut)`);
      return;
    }
    switch (ev.kind) {
      case 'silence→speaker': {
        const resumeGapMs = this.ignoredSilenceCloseMs === null
          ? Infinity
          : ev.tMs - this.ignoredSilenceCloseMs;
        this.ignoredSilenceCloseMs = null;
        this.lastAcceptedBoundaryMs = Math.max(this.lastAcceptedBoundaryMs, ev.tMs);
        if (resumeGapMs >= 0 && resumeGapMs < EARLY_SILENCE_CLOSE_MS) {
          this.log(`[boundary] keep turn open across ${Math.round(resumeGapMs)}ms silence wobble`);
          break;
        }
        this.openTurn(ev.tMs);
        break;
      }
      case 'speaker→speaker':
      case 'overlap-onset':
      case 'overlap-offset':
        this.ignoredSilenceCloseMs = null;
        this.lastAcceptedBoundaryMs = Math.max(this.lastAcceptedBoundaryMs, ev.tMs);
        this.closeTurn(ev.tMs);   // hard-split at the change / overlap edge
        this.openTurn(ev.tMs);
        break;
      case 'speaker→silence':
        this.ignoredSilenceCloseMs = null;
        this.lastAcceptedBoundaryMs = Math.max(this.lastAcceptedBoundaryMs, ev.tMs);
        this.closeTurn(ev.tMs, SILENCE_CLOSE_CONTEXT_MS);
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

  private closeTurn(t1: number, contextPadMs = 0): void {
    this.queue.push({ kind: 'close', t1, contextPadMs });
    void this.pump();
  }

  /**
   * Exact retroactive cut from the ring, with the layout needed to read its timestamps back.
   *
   * A span may contain HOLES — instants no frame ever covered, because capture dropped a buffer,
   * gated a silence, or the source renegotiated. The cut concatenates only what exists, so the
   * audio handed to STT is SHORTER than the span it covers and its internal clock is not the wall
   * clock. Zero-filling the holes would restore that equality at the price of inviting the model to
   * hallucinate over manufactured silence, so the audio stays compressed and `spans` records which
   * wall instant each run of it came from. Partial frames at the edges are sliced by sample offset.
   */
  private cut(t0: number, t1: number): { pcm: Float32Array; spans: CutSpan[] } {
    const parts: Float32Array[] = [];
    const spans: CutSpan[] = [];
    let total = 0;
    for (const f of this.ring) {
      const fStart = f.tMs;
      const fEnd = f.tMs + (f.pcm.length / SAMPLE_RATE) * 1000;
      if (fEnd <= t0) continue;
      if (fStart >= t1) break;
      const from = Math.max(0, Math.round(((t0 - fStart) / 1000) * SAMPLE_RATE));
      const to = Math.min(f.pcm.length, Math.round(((t1 - fStart) / 1000) * SAMPLE_RATE));
      if (to <= from) continue;
      const wallStartMs = fStart + (from / SAMPLE_RATE) * 1000;
      const prev = spans[spans.length - 1];
      // Frames that abut in wall time are ONE run; a jump opens a new one. Measuring against the
      // previous run's end is what makes a hole a hole rather than a per-frame accounting artefact.
      if (prev && Math.abs(wallStartMs - (prev.wallStartMs + (prev.samples / SAMPLE_RATE) * 1000)) < 1) {
        prev.samples += to - from;
      } else {
        spans.push({ fromSample: total, wallStartMs, samples: to - from });
      }
      parts.push(f.pcm.subarray(from, to));
      total += to - from;
    }
    const out = new Float32Array(total);
    let off = 0;
    for (const p of parts) { out.set(p, off); off += p.length; }
    return { pcm: out, spans };
  }

  /**
   * Compressed audio time (seconds into a cut) → wall time (ms).
   *
   * Without it every word after a hole is stamped early by the accumulated hole and the error
   * compounds across the turn: turns open and close against the wrong instants, and the hint binder
   * matches names against a clock that has drifted away from the meeting's.
   */
  private wallTimeAt(spans: CutSpan[], sec: number, fallbackMs: number, edge: 'start' | 'end' = 'start'): number {
    if (!spans.length) return fallbackMs + Math.max(0, sec) * 1000;
    const raw = Math.round(Math.max(0, sec) * SAMPLE_RATE);
    // A time landing exactly on a run boundary is ambiguous, and the two edges resolve it opposite
    // ways: a START opens the next run, an END closes the previous one. Resolving an end from the
    // last sample it covers is what stops a sentence being stretched across the hole that follows it.
    const sample = edge === 'end' ? Math.max(0, raw - 1) : raw;
    for (const s of spans) {
      if (sample < s.fromSample + s.samples) {
        const off = Math.max(0, sample - s.fromSample) + (edge === 'end' ? 1 : 0);
        return s.wallStartMs + (off / SAMPLE_RATE) * 1000;
      }
    }
    const last = spans[spans.length - 1];
    return last.wallStartMs + (last.samples / SAMPLE_RATE) * 1000;
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
      if (closeAtEnd || this.disposed) {
        // Session end leaves nothing uncovered. If the cut source closed the last turn and never
        // re-opened, everything spoken since that close is still sitting in the ring unsubmitted —
        // the same coverage gap openTurnApply folds away mid-session, at the one instant no later
        // boundary can arrive to do it.
        let last = this.turn;
        if (!last && this.latestAudioMs - this.confirmedHighWaterMs >= MIN_SUBMIT_MS) {
          try {
            last = await this.openTurnApply({ t0: this.confirmedHighWaterMs, segId: `seg_${this.segCounter++}` });
            last.t1 = this.latestAudioMs;
          } catch (e: any) { this.log(`[ChunkedTranscriber] final coverage turn failed: ${e?.message}`); }
        }
        if (last) {
          // Session end is itself the final boundary. An earlier model close may
          // have been ignored, so carry the open turn through the last captured
          // sample before the closing submission.
          last.t1 = Math.max(last.t1, this.latestAudioMs);
          this.lastAudioEndMs = Math.max(this.lastAudioEndMs, last.t1);
          this.turn = null;
          await this.submitTurn(last, true).catch((e: any) =>
            this.log(`[ChunkedTranscriber] turn close failed: ${e?.message}`));
        }
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
  private async openTurnApply(item: { t0: number; segId: string }): Promise<Turn> {
    this.commitCounter++;
    if (this.turn) { const prev = this.turn; this.turn = null; await this.submitTurn(prev, true); }
    // CONTIGUOUS COVERAGE. A turn begins where the last one ended, not where the cut says. The
    // boundary stream answers "where did the speaker change" — it does not get to answer "which
    // audio exists". A close whose re-open never fires leaves a span no turn covers, and audio
    // there is ringed, fed to the model, and never submitted: words the room said that the
    // transcript can never contain. Starting at the coverage mark folds that span into this
    // turn's first window instead, and is the same clamp as before when there is no gap (a
    // flicker opening INSIDE confirmed audio still starts at the high-water, never re-transcribing
    // it). COVERAGE_BACKFILL_MAX_MS bounds how far back a genuine long silence may reach.
    const covered = this.confirmedHighWaterMs;
    const t0 = covered > 0 ? Math.max(covered, item.t0 - COVERAGE_BACKFILL_MAX_MS) : item.t0;
    // Real silence since the last turn → reset the in-memory prompt so the new utterance starts
    // clean (no inherited context, no silence-hallucination loop). Judged on the BOUNDARY's own
    // instant: that is when speech resumed, and it is unaffected by how far the span back-extends.
    if (this.lastAudioEndMs > 0 && item.t0 - this.lastAudioEndMs >= SILENCE_PROMPT_RESET_MS) {
      this.lastConfirmedText = '';
    }
    this.turn = {
      clusterId: item.segId, turnId: this.turnCounter++,
      t0, t1: t0, confirmedUpToMs: t0,
      history: [], seq: 0, lastSubmitEndMs: 0, firstSubmitDone: false, allConfirmed: [], pendingName: null, pendingTail: [],
      draftedUpToSeq: 0, lastVoicedWallMs: Date.now(), resolvedName: null,
    };
    return this.turn;
  }

  /** Close the open turn at a boundary: everything confirms (last chance). */
  private async closeTurnApply(item: { t1: number; contextPadMs?: number }): Promise<void> {
    if (!this.turn) return;
    const t = this.turn;
    // Clamp the boundary to available audio and never below confirmed.
    t.t1 = Math.max(t.confirmedUpToMs, Math.min(item.t1, this.latestAudioMs || item.t1));
    if (item.contextPadMs && item.contextPadMs > 0) {
      t.contextEndMs = Math.max(t.t1, Math.min(t.t1 + item.contextPadMs, this.latestAudioMs || t.t1));
    }
    this.lastAudioEndMs = Math.max(this.lastAudioEndMs, t.t1);
    this.turn = null;
    await this.submitTurn(t, true);
  }

  /** One submission of the turn's unconfirmed window. While the turn is open,
   *  confirmation is LocalAgreement-2 (the bot's word-prefix stability); on
   *  close everything confirms. */
  private async submitTurn(turn: Turn, closing: boolean): Promise<void> {
    // Never ask for audio the ring has already evicted. `cut` can only return what it still holds,
    // so a span reaching further back yields RECENT audio under an OLD start time — the whole turn
    // then lands at the wrong instant. Clamping to the oldest surviving frame keeps the claim and
    // the samples the same age; audio older than the ring is gone and pretending otherwise is worse
    // than admitting it.
    const oldestRingMs = this.ring.length ? this.ring[0].tMs : turn.confirmedUpToMs;
    const spanStart = Math.max(turn.confirmedUpToMs, oldestRingMs);
    // Open turns read to the LIVE AUDIO EDGE, not the last commit — pending
    // tracks speech in near-real-time and stability builds at tick cadence.
    // The trailing un-committed second may belong to the next speaker; the
    // LocalAgreement tail guard (the last forming words never confirm) keeps
    // that bleed out of confirmed output until segmentation rules on it.
    // Closing turns read exactly to the committed boundary, PLUS a small trailing
    // STT-only context pad (contextEndMs) so the final phones survive — published
    // timestamps still clip to publishEnd (the committed speech boundary).
    const publishEnd = closing ? turn.t1 : Math.max(turn.t1, this.latestAudioMs || turn.t1);
    const spanEnd = closing ? Math.max(publishEnd, turn.contextEndMs ?? publishEnd) : publishEnd;
    // THE SUBMIT NARRATION. Every audio-time span this turn asks about, and the reason any of them
    // produces no text, on one grep-able line each. Without it a hole in the transcript cannot be
    // told apart from audio that was never submitted, and both were guessed at.
    const narrate = (verdict: string): void =>
      this.log(`[submit] turn=${turn.turnId} span=[${Math.round(spanStart)},${Math.round(spanEnd)}] `
        + `${((spanEnd - spanStart) / 1000).toFixed(2)}s closing=${closing ? 1 : 0} ${verdict}`);
    if (spanEnd - spanStart < (closing ? 250 : MIN_SUBMIT_MS)) {
      narrate('SKIP too-short');
      if (closing) await this.closeOut(turn);
      return;
    }
    // No new audio since the last pass — an identical window returns
    // identical text and confirms nothing; don't waste the call.
    if (!closing && spanEnd - turn.lastSubmitEndMs < 500) { narrate('SKIP no-new-audio'); return; }
    turn.lastSubmitEndMs = spanEnd;
    // A real submission is now proceeding (past the too-short/no-new-audio gates): the
    // first-submit fast path has fired for this turn and must not re-fire on the 1s heartbeat.
    turn.firstSubmitDone = true;

    const { pcm, spans } = this.cut(spanStart, spanEnd);
    if (pcm.length < SAMPLE_RATE * 0.2 || rms(pcm) < DROP_RMS) {
      narrate(`SKIP rms=${rms(pcm).toFixed(4)} samples=${pcm.length}`);
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
      narrate(result ? `DROP gated text=${JSON.stringify((result.text || '').slice(0, 40))}` : 'DROP stt-failed');
      if (closing) await this.closeOut(turn);
      return;
    }

    // Map whisper segments from the CUT's timebase to wall time. Whisper describes the audio it was
    // handed, which is the span minus its holes — so the two clocks agree only when the span was
    // gapless, and `spans` is what carries the difference.
    const lang = this.cb.language || result!.language || 'en';
    const mapped = gated.map((ws) => {
      const startMs = this.wallTimeAt(spans, ws.start || 0, spanStart);
      const rawEndMs = this.wallTimeAt(spans, ws.end || 0, spanStart, 'end');
      const endMs = Math.min(publishEnd, rawEndMs || publishEnd) || publishEnd;
      return { text: ws.text.trim(), startMs, endMs, language: lang };
    }).filter(s => {
      if (!s.text) return false;
      // The trailing STT context pad is for recognition only — drop anything that
      // begins past the committed speech boundary, and any zero/negative span.
      if (closing && s.startMs >= publishEnd) return false;
      if (s.endMs <= s.startMs) return false;
      // Prompt echo — whisper parroting the initial_prompt back. Targeted
      // check; the blanket phrase list would also kill legit short answers
      // ("Yes.") inside real-speech windows the RMS gate already vouched for.
      if (prompt && s.text.length > 6 && prompt.includes(s.text)) return false;
      return true;
    });
    if (mapped.length === 0) {
      narrate(`DROP all-filtered raw=${JSON.stringify(gated.map(g => g.text).join(' ').slice(0, 60))}`);
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
    narrate(`OK confirm=${confirmCount}/${mapped.length} text=${JSON.stringify(mapped.map(m => m.text).join(' ').slice(0, 80))}`);

    const name = this.resolveName(turn);
    if (turn.pendingName && turn.pendingName !== name) this.cb.clearPending(turn.pendingName);

    const confirmed: ChunkSegment[] = mapped.slice(0, confirmCount).map(s => ({
      text: s.text, startMs: s.startMs, endMs: s.endMs, language: s.language,
      segmentId: `turn:${turn.turnId}:${turn.seq++}`,
    }));

    // The forming tail carries the ids it will CONFIRM under (turn.seq is already past this
    // pass's confirmations). A segment_id is an identity — the store upserts on it — so a draft
    // published under an id of its own would never be replaced by its confirmation, and every
    // sentence would end up stored twice.
    const tail: ChunkSegment[] = mapped.slice(confirmCount).map((s, i) => ({
      text: s.text, startMs: s.startMs, endMs: s.endMs, language: s.language,
      segmentId: `turn:${turn.turnId}:${turn.seq + i}`,
    }));
    // Whisper re-segments as the window grows, so a pass can produce FEWER pieces than the draft
    // it replaces — and the ids above it are then never written again. The reader keeps a
    // half-sentence sitting beside its own confirmation forever. Clear those ids the way the
    // per-channel lane finalizes its drafts: an empty-text row drops the draft (transcript.v1).
    const stale = this.staleDrafts(turn, turn.seq + (closing ? 0 : tail.length), lang, spanStart);

    if (confirmCount > 0) {
      // ONE bundle: confirmed + surviving tail. Splitting them deletes the
      // client's pending block for seconds (the "vanishing transcript" bug).
      this.cb.publish(name, confirmed, closing ? stale : [...tail, ...stale]);
      this.rememberPublishedSpeaker(name, confirmed[confirmed.length - 1]?.endMs);
      turn.allConfirmed.push(...confirmed);
      // Track per-key so a later name change repaints these in place.
      let cs = this.clusterSegments.get(turn.clusterId);
      if (!cs) { cs = []; this.clusterSegments.set(turn.clusterId, cs); }
      cs.push(...confirmed);
      this.clusterName.set(turn.clusterId, name);
      turn.confirmedUpToMs = mapped[confirmCount - 1].endMs;
      this.confirmedHighWaterMs = Math.max(this.confirmedHighWaterMs, turn.confirmedUpToMs);
      const txt = confirmed.map(s => s.text).join(' ');
      this.lastConfirmedText = (this.lastConfirmedText + ' ' + txt).slice(-PROMPT_TAIL_CHARS * 2);
      turn.pendingName = !closing && tail.length > 0 ? name : null;
      turn.pendingTail = closing ? [] : tail;
    } else if (!closing) {
      turn.pendingTail = tail;
      if (tail.length > 0 || stale.length > 0) {
        if (tail.length > 0) turn.pendingName = name;
        this.cb.publishPending(name, [...tail, ...stale]);
      } else if (turn.pendingName) {
        this.cb.clearPending(turn.pendingName);
        turn.pendingName = null;
      }
    }

    if (closing) {
      await this.closeOut(turn);
    }
  }

  /**
   * Empty-text rows for every segment id this turn published above `writtenTo`, and the new
   * high-water of ids it has ever used.
   *
   * The consumer upserts on segment_id, so an id that is written once and never again keeps
   * whatever it last said. A draft split into three pieces whose confirmation comes back as one
   * therefore leaves two orphan half-sentences beside the whole one — the reader sees the text
   * twice, and no amount of correct confirming removes it. An empty-text row drops the draft
   * (transcript.v1's draft contract), which is the same instrument the per-channel lane uses.
   */
  private staleDrafts(turn: Turn, writtenTo: number, language: string, atMs: number): ChunkSegment[] {
    const out: ChunkSegment[] = [];
    for (let s = writtenTo; s < turn.draftedUpToSeq; s++) {
      out.push({ text: '', startMs: atMs, endMs: atMs, language, segmentId: `turn:${turn.turnId}:${s}` });
    }
    turn.draftedUpToSeq = writtenTo;
    return out;
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
      this.rememberPublishedSpeaker(name, promoted[promoted.length - 1]?.endMs);
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
      if (turn.resolvedName) return;
      const name = this.resolveName(turn);
      if (name === turn.clusterId) {
        this.unresolved.push({ clusterId: turn.clusterId, t0: turn.t0, t1: turn.t1, blockedNames: turn.blockedNames });
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
    const commit = { clusterId: turn.clusterId, tStartMs: turn.t0, tEndMs: turn.t1 };
    // recordVote:false — we only commit a vote once the result survives the
    // short-UI-switch guard below, so a held-provisional bad hint never votes.
    const r = this.binder.resolve(commit, { recordVote: false });
    if (r.source !== 'provisional-cluster-id' && this.shouldDeferShortUiSwitch(turn, r.speakerName, r.source)) {
      // A brief isolated tile flip to a NEW name right after a different speaker:
      // hold provisional and block that name from a later claim/rename.
      if (!turn.blockedNames) turn.blockedNames = new Set();
      turn.blockedNames.add(r.speakerName);
      this.log(`[ChunkedTranscriber] short UI switch held provisional ${turn.clusterId}; speaker=${r.speakerName}`);
      return turn.clusterId;
    }
    if (r.source !== 'provisional-cluster-id') {
      this.binder.recordClusterVote(turn.clusterId, r.speakerName);
      turn.resolvedName = r.speakerName;
    } else {
      const d = this.binder.explainMatch(commit);
      const b = d.best ? ` best=${d.best.name} support=${Math.round(d.best.supportMs)}ms cov=${d.best.coverage.toFixed(2)} conf=${d.best.confidence.toFixed(2)}` : '';
      this.log(`[binder-reject] ${turn.clusterId} [${Math.round(turn.t0)},${Math.round(turn.t1)}] reason=${d.reject} candidates=${d.candidates} flickerSkipped=${d.flickerSkipped}${b}`);
    }
    return r.speakerName;
  }

  private isRealSpeakerName(name: string): boolean {
    return !!name && !/^seg_\d+$/.test(name);
  }

  private rememberPublishedSpeaker(name: string, endMs?: number): void {
    if (!this.isRealSpeakerName(name) || endMs === undefined) return;
    this.lastPublishedSpeaker = { name, endMs };
  }

  /** A short, isolated active-speaker window-match to a NEW name immediately after a
   *  different published speaker. With no acoustic evidence to confirm it, a brief
   *  tile flip is more likely a stale/echoed hint than a real sub-{MAX}ms turn — so
   *  hold it provisional rather than stamp a confident wrong name. */
  private shouldDeferShortUiSwitch(turn: Turn, speakerName: string, source: string): boolean {
    if (source !== 'window-match') return false;
    if (!this.isRealSpeakerName(speakerName)) return false;
    const prev = this.lastPublishedSpeaker;
    if (!prev || prev.name === speakerName) return false;
    const durationMs = Math.max(0, turn.t1 - turn.t0);
    if (durationMs <= 0 || durationMs > SHORT_UI_SWITCH_MAX_MS) return false;
    const gapMs = Math.max(0, turn.t0 - prev.endMs);
    return gapMs <= SHORT_UI_SWITCH_GAP_MS;
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
