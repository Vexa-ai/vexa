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
import {
  CsrcTurnSource, PyannoteTurnSource,
  type TransportEvent, type TurnSource, type TurnSourceCallbacks,
} from './turn-source.js';
import { TrackNamer } from './track-namer.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import { localAgreement } from '@vexa/transcribe-buffer';
import { hallucinationRule, type SuppressedSegment } from './hallucination-gate.js';

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
/** PROVISIONAL CUT. A continuous monologue produces no boundary, so its first FINAL used to wait
 *  for the speaker to pause — measured at ~9 s on a live meeting, which is the founder's latency
 *  complaint. Past this much unconfirmed audio the open turn is rolled: everything so far confirms
 *  and a fresh turn continues on the SAME track, so attribution is untouched and only the
 *  granularity of the finals changes. Well inside TURN_MAX_MS, which remains the Whisper-window
 *  bound rather than a latency control. Tunable via VEXA_PROVISIONAL_CUT_MS. */
const PROVISIONAL_CUT_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_PROVISIONAL_CUT_MS) || 4000);
/** Pyannote's speech-end frame can land a little early. On a clean
 *  speaker→silence close, send a small trailing context pad to STT so final
 *  phones/words survive, while clipping published timestamps to the committed
 *  speech boundary. Speaker→speaker cuts get no pad: attribution wins there. */
const SILENCE_CLOSE_CONTEXT_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_SILENCE_CLOSE_CONTEXT_MS) || 350);
/** TTL idle-finalize: if the open turn has unconfirmed pending and no VOICED update
 *  has arrived for this long (speaker paused / segmenter didn't fire a close on
 *  continuous live-mixed audio), commit the pending now instead of waiting. Pairs
 *  with the stricter 3-pass agreement so it never strands text. Above natural
 *  sentence pauses, below an awkward wait. */
const CONFIRM_TTL_MS = 2500;
/** Don't bother Whisper with unconfirmed windows shorter than this unless
 *  the turn is closing. */
const MIN_SUBMIT_MS = 800;
/** GAP RECLAIM bounds. A gap between two turns shorter than MIN is boundary jitter and
 *  already covered by the close pad; longer than MAX is a real pause, and dragging it
 *  into the next Whisper window would cost latency and invite silence hallucination.
 *  Between them the gap is submitted with the next turn if it carries energy — the
 *  segmenter's false negatives stop deleting speech. Tunable via VEXA_GAP_RECLAIM_MAX_MS. */
const GAP_RECLAIM_MIN_MS = 400;
const GAP_RECLAIM_MAX_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_GAP_RECLAIM_MAX_MS) || 10_000);
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
/** Confidence a DIFFERENT name must reach, over the OPEN turn's grown window, to take a turn
 *  already attributed to someone else (and repaint what it published).
 *
 *  It is deliberately an absolute bar rather than a lead over the incumbent's remembered
 *  confidence: the incumbent's number was measured on a much SMALLER window (often the first
 *  sub-second tick, where it was the only name lit and so scored 1.0), and comparing across
 *  windows of different size is not a comparison at all. Confidence here is the winner's share
 *  of all support in the CURRENT window, so this bar means "a clear majority of the evidence
 *  seen so far", strictly above the MIN_MATCH_CONFIDENCE gate a first attribution must clear.
 *  Hysteresis, so two near-tied names cannot thrash segment by segment. Tunable via
 *  VEXA_REATTRIBUTE_MIN_CONFIDENCE. */
const REATTRIBUTE_MIN_CONFIDENCE = Number((typeof process !== 'undefined' && process.env?.VEXA_REATTRIBUTE_MIN_CONFIDENCE) || 0.75);
/** Share of the lit-time over a turn's window that ONE name must hold before the late-box
 *  claim will hand it that turn. Below this the tiles disagree too much to name the turn from
 *  the UI alone and it stays provisional ("Speaker"). Tunable via VEXA_CLAIM_MIN_SHARE. */
const CLAIM_MIN_SHARE = Number((typeof process !== 'undefined' && process.env?.VEXA_CLAIM_MIN_SHARE) || 0.6);
/** TURN-SOURCE ARMING (mode 'auto'). The transport spine cannot be authoritative before it has
 *  said anything — an unarmed spine emits no turns at all, which is a HOLE in the transcript, not
 *  a fallback. So pyannote carries the meeting from frame one and the transport takes over on its
 *  first transition. If none arrives within this much audio, the transport is disarmed for the
 *  session and one `turn-source-fallback` observation says so. Tunable via VEXA_TURN_SOURCE_GRACE_MS. */
const TURN_SOURCE_GRACE_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_TURN_SOURCE_GRACE_MS) || 20_000);
/** ORPHAN CONTAINMENT. A turn that carries no track — a span the transport could not assign — is
 *  published as "Speaker". Some of those are genuinely unassignable (two people talking at once);
 *  others sit entirely inside one speaker's run with nobody else audible anywhere in them, and
 *  calling those "Speaker" is a gap we can close without guessing. An orphan inherits a track's name
 *  only when EXACTLY ONE track is a candidate: audible somewhere in the span, or the immediately
 *  preceding turn if the orphan begins within this window of it. Two candidates and it stays
 *  honest. Tunable via VEXA_ORPHAN_ADJACENT_MS. */
/** What an unattributable piece is published as. The host renders a provisional segmentation id the
 *  same way; naming it here keeps the two spellings of "we do not know" in one place. */
const PROVISIONAL_SPEAKER = 'Speaker';
/** How much of the just-confirmed text is remembered for the re-emission check. */
const TAIL_DEDUP_CHARS = 120;
/** Shortest repeat treated as a re-emission rather than as speech. Two tokens is a real thing a
 *  person says twice ("да, да"); this is deliberately above that. */
const TAIL_DEDUP_MIN_TOKENS = 3;
/** Only windows starting this close behind the confirmed boundary can be re-emitting it. */
const TAIL_DEDUP_REACH_MS = 4000;
/** How far either side of a contested instant to count tile re-assertions. A speaker's outline is
 *  re-asserted about every 2 s, so a second either way catches the current speaker without reaching
 *  into the neighbouring turn. Tunable via VEXA_CONTESTED_HINT_TOLERANCE_MS. */
const CONTESTED_HINT_TOLERANCE_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_CONTESTED_HINT_TOLERANCE_MS) || 1000);
/** Tile re-assertions a participant must have somewhere in the meeting before the tiles may be used
 *  to rule FOR or AGAINST them in a contested span. */
const CONTESTED_MIN_TILE_EVIDENCE = Number((typeof process !== 'undefined' && process.env?.VEXA_CONTESTED_MIN_TILE_EVIDENCE) || 3);
/** Comparison tokens: case- and punctuation-insensitive, so "глюнки." matches "глюнки". */
const tokens = (t: string): string[] => (t.toLowerCase().match(/[\p{L}\p{N}']+/gu) ?? []);
const ORPHAN_ADJACENT_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_ORPHAN_ADJACENT_MS) || 1500);

export interface ChunkSegment {
  text: string;
  /** Audio-time ms (same timebase as feedAudio's tsMs). */
  startMs: number;
  endMs: number;
  language: string;
  /** Stable suffix — host prefixes its session uid. Same id on rename. */
  segmentId: string;
}

/** The cut source: streams frames, emits boundaries via the sink set at construction.
 *  PyannoteSegmenter in production; injectable for tests. Declared with the spine it belongs to
 *  (turn-source.ts) and re-exported here so every existing importer is unaffected. */
export type { BoundarySource } from './turn-source.js';
import type { BoundarySource } from './turn-source.js';

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
  /** A segment the hallucination gate refused to publish. Suppression is a JUDGEMENT the
   *  lane made about real STT output, so it is reported, never silent — the host logs it as
   *  an observation and can audit what the gate ate. */
  onSuppressed?: (s: SuppressedSegment) => void;
  /** Instantaneous per-hint outcome (the hint-hop instrument): 'matched' when the
   *  hint names/claims a turn at the moment it arrives (or re-asserts the open
   *  turn's already-resolved name); 'missed' when no turn overlaps it yet. A
   *  'missed' hint is still recorded in the binder and may window-match a later
   *  commit — this reports the hop's immediate fate, not the final binding. */
  onHintOutcome?: (o: { name: string; kind: HintKind; tMs: number; outcome: 'matched' | 'missed' }) => void;
  /** WHERE turn edges come from (turn-source.ts).
   *   'pyannote' — today's lane, unchanged. The default, so every existing caller is untouched.
   *   'csrc'     — the transport spine, authoritative from the first frame (offline A/B only:
   *                a live meeting whose client does not mix server-side would produce nothing).
   *   'auto'     — pyannote carries the session until the transport says something, then the
   *                transport takes over; a dead or absent transport falls back, mid-meeting,
   *                without touching the ring. This is the production setting. */
  turnSource?: 'pyannote' | 'csrc' | 'auto';
  /** How long 'auto' waits for a first transport transition before disarming it. */
  turnSourceGraceMs?: number;
  /** OUR OWN display name. The bot is a participant in every meeting it records, so without this
   *  the namer cannot tell its own name in a roster from a person's — and on the m34 meeting that
   *  is exactly how a bot's name ended up on a human's speech. */
  selfName?: string;
  /** Typed lane observations — `turn-source-armed` / `turn-source-fallback`. The host stores them
   *  beside the tape, so a replay can answer WHICH SPINE produced a transcript, which is otherwise
   *  unrecoverable from the transcript itself. */
  onObservation?: (o: TurnSourceObservation) => void;
  /**
   * VIRTUAL TIME — a harness seam, not a behaviour switch.
   *
   * Two things in this lane are driven by the wall clock rather than by audio time: the 1 s
   * heartbeat that ticks/rolls/finalizes the open turn, and the TTL that commits a pending tail
   * when no voiced update has arrived. They are why a replay had to run at 1x to reproduce a
   * cadence defect — a turn transcribed in several growing passes live is transcribed in ONE pass
   * when the tape is fired instantly, and the defects that live in the cadence vanish.
   *
   * Injecting the clock lets a replay drive those same code paths from the TAPE'S OWN timestamps
   * and execute in seconds. The time CONSTANTS are untouched — this changes what "now" means, not
   * what the lane does with it. Production leaves it undefined and uses Date.now/setInterval.
   */
  clock?: LaneClock;
}

/** The wall-clock dependencies of the lane, in one injectable surface. */
export interface LaneClock {
  now(): number;
  /** Register the lane's periodic heartbeat. Returns its canceller. */
  setHeartbeat(fn: () => void, everyMs: number): () => void;
}

/** The lane's account of which spine it is running on, and why it changed. */
export interface TurnSourceObservation {
  type: 'turn-source-armed' | 'turn-source-fallback';
  from: 'pyannote' | 'csrc';
  to: 'pyannote' | 'csrc';
  reason: 'transport-armed' | 'no-transport-signal' | 'transport-silent';
  tMs: number;
  detail?: Record<string, unknown>;
}

interface RingFrame { pcm: Float32Array; tMs: number }
/** Segmentation lifecycle items on the serialized queue: a boundary opens a turn
 *  (speech start / speaker change) or closes the open one (speaker change / end). */
type SegItem =
  | { kind: 'open'; t0: number; segId: string; trackId?: string; contested?: boolean }
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
  /** Window-match confidence the current `resolvedName` was accepted at. A challenger
   *  must beat it by REATTRIBUTE_MARGIN over the turn's GROWN window to take the turn. */
  resolvedConfidence?: number;
  /** Set at close: the turn is over, its name is final, no further re-resolution. */
  nameLocked?: boolean;
  /** Optional STT-only trailing context used on speech-end closes. Published
   *  timestamps and confirmed high-water still stop at t1. */
  contextEndMs?: number;
  /** Names this turn must NOT be (re)claimed to — a short-UI-switch hint that was
   *  held provisional, so a later claim/rename can't resurrect the bad name. */
  blockedNames?: Set<string>;
  /** The transport's id for this turn's speaker (csrc spine only). When set, naming is the
   *  TrackNamer's job and the hint claim/re-resolution machinery is bypassed entirely. */
  trackId?: string;
  /** Two or more sources were audible across this span. The mix cannot be split, so this turn is
   *  published unattributed — never merged into either speaker's name. */
  contested?: boolean;
  /** Set once this turn has been counted as an orphan (resolveName runs many times per turn). */
  orphanCounted?: boolean;
  /** Every track audible at some point inside this turn's span (transport spine). One entry means
   *  the span is unambiguous even when the transport declined to own it. `undefined` means the
   *  spine never got to say — which is NOT the same as "nobody else was there". */
  spanTracks?: string[];
  /** The transport's live edge for the OPEN turn: audio up to here belongs to it and no further.
   *  Absent ⇒ read to the newest audio frame, which is what the pyannote spine has always done. */
  edgeMs?: number;
}
/** A committed turn whose hint hasn't arrived yet (provisional segmentation id) —
 *  re-resolved when a later hint produces a window match. Segments live in
 *  clusterSegments, so only the window + key are kept here. */
interface UnresolvedTurn { clusterId: string; t0: number; t1: number; blockedNames?: Set<string> }

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

  // ── the turn spine ────────────────────────────────────────────────────────
  /** Both spines run for the whole session; exactly one is AUTHORITATIVE at a time. Keeping the
   *  loser warm is what makes a mid-meeting fallback seamless: the segmenter has already locked
   *  on, the ring is shared and untouched, and the switch is one flag plus a clean cut. */
  private pyannoteSource: PyannoteTurnSource | null = null;
  private csrcSource: CsrcTurnSource | null = null;
  private authoritative: 'pyannote' | 'csrc' = 'pyannote';
  private mode: 'pyannote' | 'csrc' | 'auto' = 'pyannote';
  private transportDisarmed = false;
  private readonly graceMs: number;
  /** Names for transport tracks — earned from evidence, never guessed. */
  private readonly trackNamer: TrackNamer;
  /** trackId → the segmentation keys it has published under, so a late name repaints all of them. */
  private trackClusters = new Map<string, Set<string>>();
  /** The track that owned the previously closed turn — gap reclaim may not cross a speaker change. */
  private lastClosedTrackId: string | undefined;
  /** Where the previous turn ended — the adjacency half of the orphan-containment test. */
  private lastClosedEndMs = 0;
  /** Orphans offered to containment, and those it accepted. Reported, so the rule's own refusal
   *  rate is visible rather than assumed. */
  private orphanTurns = 0;
  private orphanContained = 0;
  private contestedTurns = 0;

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
  /** The tail of the most recently confirmed text — what a re-emission would repeat. */
  private lastConfirmedTail = '';
  /** Audio-time end of the last processed commit — used to detect the silence
   *  gap that resets the prompt (SILENCE_PROMPT_RESET_MS). */
  private lastAudioEndMs = 0;
  private commitCounter = 0;
  private suppressedCount = 0;
  private turnCounter = 0;
  private disposed = false;

  /** The open turn. */
  private turn: Turn | null = null;
  private idleTimer: ReturnType<typeof setInterval> | null = null;
  private cancelHeartbeat: (() => void) | null = null;
  /** The lane's "now". Wall clock in production; the tape's clock under a virtual-time replay. */
  private now: () => number = () => Date.now();

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
    this.mode = cb.turnSource ?? 'pyannote';
    this.graceMs = cb.turnSourceGraceMs ?? TURN_SOURCE_GRACE_MS;
    if (cb.clock) this.now = () => cb.clock!.now();
    this.trackNamer = new TrackNamer({
      selfName: cb.selfName,
      onNamed: (trackId, name) => this.onTrackNamed(trackId, name),
      log: (m) => this.log(`[track-namer] ${m}`),
    });
  }

  /** Audio-accounting trace (VEXA_TRACE_SPANS=1): every window this lane submits to STT,
   *  and every window it declines to. Off by default; the only way to separate the case where
   *  STT heard the audio and got it wrong from the case where the lane never sent it. */
  private trace(msg: string): void {
    if (typeof process !== 'undefined' && process.env?.VEXA_TRACE_SPANS) this.log(`[spans] ${msg}`);
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
    // The spine. PyannoteTurnSource wraps the segmenter EXACTLY as handleBoundary used to read it,
    // so the default path is the same lane through a new seam; CsrcTurnSource is built alongside it
    // whenever the transport may be consulted, and both are fed every frame.
    const makeSegmenter = cb.makeSegmenter
      ?? ((onBoundary) => PyannoteSegmenter.create({ inferIntervalMs: 500, onBoundary }));
    t.pyannoteSource = await PyannoteTurnSource.create(
      t.sourceCallbacks('pyannote'),
      async (onBoundary) => {
        const src = await makeSegmenter(onBoundary as (ev: BoundaryEvent) => void);
        t.segmenter = src;             // kept for reset()/dispose(), exactly as before
        return src;
      },
      (m) => t.log(`[ChunkedTranscriber] ${m}`),
    );
    if (t.mode !== 'pyannote') {
      t.csrcSource = new CsrcTurnSource(t.sourceCallbacks('csrc'), {
        onDead: (info) => t.demoteTransport(info.reason, info.tMs, { quiet_ms: info.quietMs }),
        log: (m) => t.log(`[ChunkedTranscriber] csrc: ${m}`),
      });
      // A forced csrc run is the offline A/B: the transport is authoritative from frame one and
      // there is no fallback, so the comparison measures the spine and not a blend of two.
      if (t.mode === 'csrc') t.authoritative = 'csrc';
    }
    // One 1s heartbeat drives:
    //  - TICK: resubmit the open turn up to the live audio edge on a fixed
    //    cadence (latency decoupled from boundary timing),
    //  - ROLL: bound the unconfirmed Whisper window — an over-long open turn
    //    (continuous speech, stalled stability) force-rolls into a fresh turn.
    const heartbeat = (): void => {
      if (t.pumping) return;
      // Liveness: an item enqueued during the pump's closing pass misses the
      // drain loop; with no further boundaries nothing would re-pump it.
      if (t.queue.length > 0) { void t.pump(); return; }
      if (!t.turn) return;
      // A monologue confirms on a cadence instead of waiting for a pause. Only fires while the
      // turn is genuinely growing (unconfirmed audio beyond the cut length) and has text pending,
      // so a silent open turn is not chopped into empty pieces.
      if (t.turn.trackId
        && t.turn.pendingTail.length > 0
        && t.latestAudioMs - t.turn.confirmedUpToMs > PROVISIONAL_CUT_MS) {
        const rolled = t.turn;
        t.queue.push({ kind: 'close', t1: t.latestAudioMs });
        t.queue.push(t.rollOpen(rolled, t.latestAudioMs));
        void t.pump();
        return;
      }
      if (t.latestAudioMs - t.turn.confirmedUpToMs > TURN_MAX_MS) {
        // The roll is a Whisper-window bound, not a speaker change: the successor is the SAME
        // speaker, so it inherits the track. Dropping the trackId here would hand every monologue
        // longer than TURN_MAX_MS to "Speaker A" halfway through.
        t.queue.push({ kind: 'close', t1: t.latestAudioMs });
        t.queue.push(t.rollOpen(t.turn, t.latestAudioMs));
        void t.pump();
        return;
      }
      // TTL idle-finalize: pending exists but no voiced update for CONFIRM_TTL_MS
      // (speaker paused / segmenter didn't close on continuous live audio) → commit
      // what we have by closing the turn, instead of holding it for the 3rd pass.
      if (t.turn.pendingTail.length > 0 && t.now() - t.turn.lastVoicedWallMs > CONFIRM_TTL_MS) {
        t.queue.push({ kind: 'close', t1: t.latestAudioMs });
        // On the transport spine the pause that triggered this is a pause, NOT the end of the
        // speaker's turn — the transport has not said they stopped. Reopen on the same track so
        // the audio after the pause is neither dropped nor handed to a different label.
        if (t.turn.trackId) t.queue.push(t.rollOpen(t.turn, t.latestAudioMs));
        void t.pump();
        return;
      }
      if (t.latestAudioMs - Math.max(t.turn.lastSubmitEndMs, t.turn.confirmedUpToMs) >= SUBMIT_TICK_MS) {
        t.queue.push('tick');
        void t.pump();
      }
    };
    // ONE heartbeat, through the clock seam. Identical body either way — a virtual-time replay
    // must exercise the same branches in the same order, or it is measuring a different lane.
    if (cb.clock) t.cancelHeartbeat = cb.clock.setHeartbeat(heartbeat, 1000);
    else t.idleTimer = setInterval(heartbeat, 1000);
    t.log(`[ChunkedTranscriber] ready (segmentation-cut turns, hints-only naming, LocalAgreement-3 confirmation + TTL finalize)`);
    return t;
  }

  /** One mixed-audio frame. Ring + both spines — nothing else. */
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
    // BOTH spines see every frame. The loser is kept warm precisely so a fallback costs nothing
    // but a flag: a segmenter that started locking on at the moment of failure would spend its
    // first seconds blind, which is the interval a fallback exists to protect.
    this.pyannoteSource?.onAudio(pcm, tsMs);
    this.csrcSource?.onAudio(pcm, tsMs);
    this.trackNamer.tick(this.latestAudioMs);
    this.armingWatchdog();
  }

  /** One transport transition (the CSRC sensor's edge). Feeds the spine AND the namer: the spine
   *  needs to know when, the namer needs to know who was audible while a tile was lit. */
  recordTransportEvent(ev: TransportEvent): void {
    if (this.disposed) return;
    this.trackNamer.setTrackActive(String(ev.csrc), ev.active, ev.tMs);
    if (!this.csrcSource) return;
    // PROMOTE FIRST, THEN DELIVER. The spine's callbacks check who is authoritative at the moment
    // they fire, and reconcile() fires from inside onTransportEvent — so promoting afterwards
    // DROPPED the very first turn the transport ever opened. On the adversarial m34 fixture that
    // lost the meeting's opening turn to the pyannote spine, which then published it merged with
    // the next speaker's, unattributed and unrepaintable (a turn with no track cannot be renamed
    // when its track is later named). One statement's ordering, one whole speaker's first words.
    if (!this.csrcSource.armed) this.promoteTransport(ev.tMs);
    this.csrcSource.onTransportEvent(ev);
  }

  /** The platform's own captions — a SECOND naming source for transport tracks, independent of the
   *  DOM tiles and (per the m29 fixture) in 99.8% agreement with them where either is decisive. */
  recordCaption(name: string, tMs: number): void {
    if (this.disposed || !name) return;
    this.trackNamer.recordCaption(name, tMs);
  }

  /** A name the ROSTER shows — who is in the room, which is a different question from who is
   *  speaking. Never a turn edge and never a hint: it supplies the canonical casing, and it is what
   *  makes the namer's elimination rule possible for a participant the tiles never light for. */
  recordRosterName(name: string, tMs?: number): void {
    if (this.disposed || !name) return;
    this.trackNamer.recordRosterName(name, tMs);
  }

  /** How much of the roster the producer could read: participants seen, and of those, named.
   *  Elimination is an argument from a complete set and must know when the set is not. */
  recordRosterCoverage(named: number, participants: number, tMs?: number): void {
    if (this.disposed) return;
    this.trackNamer.recordRosterCoverage(named, participants, tMs);
  }

  /** 'auto' only: the transport gets `graceMs` of audio to say something, then it is disarmed for
   *  the session. One observation, not one per frame. */
  private armingWatchdog(): void {
    if (this.mode !== 'auto' || this.transportDisarmed || !this.csrcSource) return;
    if (this.csrcSource.armed || this.authoritative === 'csrc') return;
    if (this.firstAudioMs === null || this.latestAudioMs - this.firstAudioMs < this.graceMs) return;
    this.transportDisarmed = true;
    this.log(`[ChunkedTranscriber] no transport transition within ${this.graceMs}ms — staying on the pyannote spine`);
    this.cb.onObservation?.({
      type: 'turn-source-fallback', from: 'pyannote', to: 'pyannote',
      reason: 'no-transport-signal', tMs: this.latestAudioMs, detail: { grace_ms: this.graceMs },
    });
  }

  /** The transport spoke for the first time inside the grace: it takes the spine. The open pyannote
   *  turn is cut at that instant so no span is claimed by two spines at once. */
  private promoteTransport(tMs: number): void {
    if (this.mode !== 'auto' || this.transportDisarmed || this.authoritative === 'csrc') return;
    this.authoritative = 'csrc';
    if (this.turn) this.queue.push({ kind: 'close', t1: Math.max(this.turn.t0, Math.min(tMs, this.latestAudioMs || tMs)) });
    void this.pump();
    this.log(`[ChunkedTranscriber] turn spine → csrc (transport armed at ${tMs})`);
    this.cb.onObservation?.({ type: 'turn-source-armed', from: 'pyannote', to: 'csrc', reason: 'transport-armed', tMs });
  }

  /** The transport died mid-meeting. Fall back WITHOUT touching the ring: close the open transport
   *  turn, open a fresh one immediately so the audio in flight is not dropped while pyannote waits
   *  for its next boundary, and disarm the transport for the rest of the session. */
  private demoteTransport(reason: 'transport-silent', tMs: number, detail?: Record<string, unknown>): void {
    if (this.authoritative !== 'csrc') return;
    this.authoritative = 'pyannote';
    this.transportDisarmed = true;
    const at = Math.min(tMs, this.latestAudioMs || tMs);
    if (this.turn) {
      this.queue.push({ kind: 'close', t1: Math.max(this.turn.t0, at) });
      this.queue.push({ kind: 'open', t0: at, segId: `seg_${this.segCounter++}` });
    }
    void this.pump();
    this.log(`[ChunkedTranscriber] turn spine → pyannote (${reason})`);
    this.cb.onObservation?.({ type: 'turn-source-fallback', from: 'csrc', to: 'pyannote', reason, tMs: at, detail });
  }

  /** The callback bundle one spine writes into. Events from the spine that is NOT authoritative are
   *  dropped here — one write surface, one writer, decided in exactly one place. */
  private sourceCallbacks(kind: 'pyannote' | 'csrc'): TurnSourceCallbacks {
    const mine = (): boolean => !this.disposed && this.authoritative === kind;
    return {
      turnOpened: (ev) => { if (mine()) this.openTurn(ev.t0, ev.trackId, ev.contested); },
      turnGrown: (ev) => {
        if (!mine() || !this.turn) return;
        if (this.turn.trackId !== ev.trackId) return;
        this.turn.edgeMs = Math.max(this.turn.edgeMs ?? 0, ev.tMs);
      },
      turnClosed: (ev) => {
        if (!mine()) return;
        if (this.turn && ev.tracks) this.turn.spanTracks = ev.tracks;
        // A segmenter's speech-end lands a little early and needs a trailing STT pad; a transport
        // deactivation already carries the sensor's inactivity window (800 ms as the bot composes
        // it), so padding it again would only feed Whisper more silence.
        this.closeTurn(ev.t1, ev.reason === 'silence' ? SILENCE_CLOSE_CONTEXT_MS : 0);
      },
    };
  }

  /** A close+reopen that is a WINDOW bound rather than a speaker change: the successor inherits
   *  the track (and its contested flag), so the label cannot change mid-utterance. */
  private rollOpen(turn: Turn, t0: number): SegItem {
    return {
      kind: 'open', t0, segId: `seg_${this.segCounter++}`,
      ...(turn.trackId ? { trackId: turn.trackId } : {}),
      ...(turn.contested ? { contested: true } : {}),
    };
  }

  /** A track earned its name: repaint everything it has already published, through the SAME rename
   *  path a late hint uses (stable segment ids, collector UPSERT). */
  private onTrackNamed(trackId: string, name: string): void {
    for (const clusterId of this.trackClusters.get(trackId) ?? []) this.claimTurn(clusterId, name);
    const turn = this.turn;
    if (turn?.trackId === trackId) {
      turn.resolvedName = name;
      if (turn.pendingTail.length > 0) {
        if (turn.pendingName && turn.pendingName !== name) this.cb.clearPending(turn.pendingName);
        turn.pendingName = name;
        this.cb.publishPending(name, turn.pendingTail);
      }
    }
  }

  /** Timestamped platform hint ("who's lit"). Also re-resolves turns that
   *  published provisionally — overlap evidence only, never inheritance. */
  recordHint(name: string, kind: HintKind, tMs: number, isEnd = false): void {
    this.binder.recordHint({ name, tMs, kind, isEnd });
    // The SAME hint is also name evidence for a transport track. It is fed unconditionally rather
    // than only while the transport is authoritative: a track's name is earned over the whole
    // meeting, and evidence discarded during a spine switch cannot be recovered.
    this.trackNamer.recordHint(name, tMs, isEnd);
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
      if (m && !blocked.has(m.name)) { this.claimTurn(u.clusterId, m.name); matchedNow = true; continue; }   // window-matched → repaint, drop
      // Late-box gap. The turn could not be window-matched, and the claim used to go to
      // WHICHEVER NAME'S HINT HAPPENED TO FIRE — regardless of whether that name was lit
      // during the turn at all. On Teams, where the tile lights on NOISE, a participant who
      // is TYPING emits hints continuously and therefore won this race almost every time.
      // That is what produced m24's label distribution (Jacob 65 : Dmitry 1 on a balanced
      // two-speaker conversation) and it is fabrication: an unnameable turn was given a
      // confident name chosen by timing rather than by evidence.
      //
      // Claim by the accumulated lit-time over the TURN's own window instead, and only when
      // one name holds a clear plurality of it (CLAIM_MIN_SHARE). When both tiles were lit
      // through the turn — the genuinely ambiguous case Teams cannot disambiguate from one
      // mixed stream — the turn stays provisional and publishes as "Speaker". Unknown stays
      // unknown.
      //
      // The genuine late-box gap is the case where NO tile was lit during the turn at all —
      // the platform reported the speaker only after the fact. That one still claims for the
      // hint that just fired, exactly as before. The change bites only when the turn window
      // DOES carry hint evidence, which is precisely when "whoever fired last" was choosing
      // against the record.
      let claimName: string | null = null;
      if (u.t1 >= claimFrom) {
        const evidence = this.binder.support({ clusterId: u.clusterId, tStartMs: u.t0, tEndMs: u.t1 });
        if (evidence.length === 0) claimName = name;                       // true late box — unchanged
        else {
          const lead = evidence.find((s) => !blocked.has(s.name));
          if (lead && lead.share >= CLAIM_MIN_SHARE) claimName = lead.name;   // the record, not the race
        }
      }
      if (claimName && !blocked.has(claimName)) { this.claimTurn(u.clusterId, claimName); matchedNow = true; }
      else still.push(u);
    }
    this.unresolved = still.slice(-MAX_UNRESOLVED);
    report();
  }

  /** Late-box claim: repaint a provisional turn's published segments under `name`
   *  (the speaker whose box just lit). Same path as a binder rename. */
  private claimTurn(clusterId: string, rawName: string): void {
    const name = this.render(rawName);
    const old = this.clusterName.get(clusterId) ?? clusterId;
    if (old === name) return;
    this.clusterName.set(clusterId, name);
    const segs = this.clusterSegments.get(clusterId);
    if (segs && segs.length) {
      this.cb.rename(old, name, segs);
      this.log(`[ChunkedTranscriber] late-box claim ${clusterId} → "${name}" (${segs.length} segment(s))`);
    }
  }

  stats(): {
    commits: number; turns: number; queued: number; unresolved: number; suppressed: number;
    binder: ReturnType<ClusterNameBinder['stats']>;
    spine: 'pyannote' | 'csrc'; contested: number; orphans: number; orphansContained: number;
    tracks: ReturnType<TrackNamer['stats']>;
    sources: Record<string, unknown>;
  } {
    return {
      commits: this.commitCounter, turns: this.turnCounter, queued: this.queue.length,
      unresolved: this.unresolved.length, suppressed: this.suppressedCount, binder: this.binder.stats(),
      spine: this.authoritative, contested: this.contestedTurns,
      orphans: this.orphanTurns, orphansContained: this.orphanContained,
      tracks: this.trackNamer.stats(),
      sources: {
        pyannote: this.pyannoteSource?.health(),
        ...(this.csrcSource ? { csrc: this.csrcSource.health() } : {}),
      },
    };
  }

  /** Resolves once no turn source has work in flight. A REPLAY-DRIVER seam: production never waits
   *  on the model, and this changes nothing about when frames are fed — only about when a harness
   *  is allowed to believe the lane has caught up. */
  async settled(): Promise<void> {
    await this.pyannoteSource?.settled?.();
    await this.csrcSource?.settled?.();
  }

  /** Session end: flush the carried span and close the open turn. Resolves
   *  AFTER the final turn has published — callers that publish session_end
   *  (the bot's graceful leave) must await it or the closing words are lost. */
  async dispose(): Promise<void> {
    if (this.disposed) return;
    this.disposed = true;
    if (this.idleTimer) { clearInterval(this.idleTimer); this.idleTimer = null; }
    if (this.cancelHeartbeat) { this.cancelHeartbeat(); this.cancelHeartbeat = null; }
    // Settle the namer's remaining evidence BEFORE the closing pass: a name earned in the
    // meeting's last seconds must still repaint what it owns while the rename path is alive.
    this.trackNamer.finish();
    // An in-flight pump owns the queue — wait it out, then run the closing
    // pass (pump(true) returns immediately while another pump holds the lock).
    while (this.pumping) await new Promise(r => setTimeout(r, 50));
    await this.pump(true);
    while (this.pumping) await new Promise(r => setTimeout(r, 50));
    try { this.segmenter?.reset(); } catch { /* best effort */ }
  }

  // ── Cutting (the authoritative TurnSource opens/closes turns) ──────────
  //
  // The cut used to be pyannote's alone, read straight off a BoundaryEvent here. It is now whatever
  // the authoritative spine says (turn-source.ts): the pyannote wrapper reproduces the exact same
  // mapping it always did, and the transport spine supplies edges it OBSERVED plus, uniquely, an
  // identity for the speaker behind them. Lifecycle items still go through the serialized queue, so
  // turn state stays single-threaded under the pump lock regardless of which spine produced them.

  private openTurn(t0: number, trackId?: string, contested?: boolean): void {
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
    if (trackId) this.trackNamer.noteHeard(trackId);   // fixes this track's Speaker-A/B/C order
    this.queue.push({
      kind: 'open', t0, segId: `seg_${this.segCounter++}`,
      ...(trackId ? { trackId } : {}), ...(contested ? { contested: true } : {}),
    });
    void this.pump();
  }

  private closeTurn(t1: number, contextPadMs = 0): void {
    this.queue.push({ kind: 'close', t1, contextPadMs });
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
  private async openTurnApply(item: { t0: number; segId: string; trackId?: string; contested?: boolean }): Promise<void> {
    this.commitCounter++;
    if (this.turn) { const prev = this.turn; this.turn = null; await this.submitTurn(prev, true); }
    const t0 = Math.max(item.t0, this.confirmedHighWaterMs);
    this.trace(`open turn=${this.turnCounter} at=${item.t0}${t0 !== item.t0 ? ` clamped→${t0} (highwater, ${t0 - item.t0}ms skipped)` : ''}`);
    // GAP RECLAIM. Only audio inside a turn is ever submitted, so the segmenter's
    // silence verdict is not just a cut — it DELETES speech. Measured on clean
    // single-speaker audio (270s, clean-audio-replay + a Deepgram oracle): the streaming
    // segmenter declared 131.5s of the 270s as speech, and 79 of the oracle's 251 words
    // fell outside every declared region. Whisper on the same file straight through
    // scored 7.5% WER; the lane scored 27.2%, and the difference was almost entirely
    // words never sent.
    //
    // So the span between the previous turn's end and this boundary is submitted with
    // this turn instead of dropped — bounded, and only when the gap actually carries
    // energy, so genuine silence is still never fed to Whisper (which hallucinates on
    // it). The reclaimed audio widens only the STT window: t0 (the attribution window
    // the namer resolves over) is untouched, so speaker identification sees exactly what
    // it saw before.
    //
    // ON THE TRANSPORT SPINE the reclaim is bounded by one further rule: it may not cross a
    // SPEAKER CHANGE. A gap there means the transport reported nobody audible, so any energy in it
    // is either the previous speaker's clipped tail or an onset the sensor caught late — and
    // sweeping the former into the NEXT speaker's window would put one person's words under
    // another person's name, which is the exact failure this whole change set exists to end. So a
    // gap is reclaimed only within one track's own broken run (DTX, a dropped packet train), or at
    // the very first turn. Recall is preserved where it is free; it is never bought with a
    // misattribution.
    let coverFrom = t0;
    const reclaimAllowed = !item.trackId
      || this.lastClosedTrackId === undefined
      || this.lastClosedTrackId === item.trackId;
    const gapFrom = Math.max(this.confirmedHighWaterMs, t0 - GAP_RECLAIM_MAX_MS);
    if (reclaimAllowed && t0 - gapFrom >= GAP_RECLAIM_MIN_MS) {
      const gap = this.cut(gapFrom, t0);
      if (gap.length > 0 && rms(gap) >= DROP_RMS) {
        coverFrom = gapFrom;
        this.trace(`reclaim turn=${this.turnCounter} ${gapFrom}..${t0} (${Math.round(t0 - gapFrom)}ms, rms=${rms(gap).toFixed(4)})`);
      }
    }
    // Real silence since the last turn → reset the in-memory prompt so the new
    // utterance starts clean (no inherited context, no silence-hallucination loop).
    if (this.lastAudioEndMs > 0 && t0 - this.lastAudioEndMs >= SILENCE_PROMPT_RESET_MS) {
      this.lastConfirmedText = '';
    }
    this.turn = {
      clusterId: item.segId, turnId: this.turnCounter++,
      t0, t1: t0, confirmedUpToMs: coverFrom,
      history: [], seq: 0, lastSubmitEndMs: 0, allConfirmed: [], pendingName: null, pendingTail: [],
      lastVoicedWallMs: this.now(), resolvedName: null,
      ...(item.trackId ? { trackId: item.trackId } : {}),
      ...(item.contested ? { contested: true } : {}),
    };
    if (item.contested) this.contestedTurns++;
    if (item.trackId) {
      let keys = this.trackClusters.get(item.trackId);
      if (!keys) { keys = new Set(); this.trackClusters.set(item.trackId, keys); }
      keys.add(item.segId);
    }
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
    const spanStart = turn.confirmedUpToMs;
    // Open turns read to the LIVE AUDIO EDGE, not the last commit — pending
    // tracks speech in near-real-time and stability builds at tick cadence.
    // The trailing un-committed second may belong to the next speaker; the
    // LocalAgreement tail guard (the last forming words never confirm) keeps
    // that bleed out of confirmed output until segmentation rules on it.
    // Closing turns read exactly to the committed boundary, PLUS a small trailing
    // STT-only context pad (contextEndMs) so the final phones survive — published
    // timestamps still clip to publishEnd (the committed speech boundary).
    //
    // On the TRANSPORT spine the open turn reads to the track's own live edge (turnGrown), not to
    // the newest frame in the ring: the newest frame may already belong to whoever spoke next, and
    // this is what "STT windows cut at transport turn edges" means where it actually bites — the
    // pending window. Without a transport edge the behaviour is the historic one.
    const liveEdge = turn.edgeMs !== undefined ? Math.max(turn.edgeMs, turn.t1) : (this.latestAudioMs || turn.t1);
    const publishEnd = closing ? turn.t1 : Math.max(turn.t1, liveEdge);
    const spanEnd = closing ? Math.max(publishEnd, turn.contextEndMs ?? publishEnd) : publishEnd;
    if (spanEnd - spanStart < (closing ? 250 : MIN_SUBMIT_MS)) {
      this.trace(`skip-short turn=${turn.turnId} ${spanStart}..${spanEnd} (${spanEnd - spanStart}ms) closing=${closing}`);
      if (closing) await this.closeOut(turn);
      return;
    }
    // No new audio since the last pass — an identical window returns
    // identical text and confirms nothing; don't waste the call.
    if (!closing && spanEnd - turn.lastSubmitEndMs < 500) return;
    turn.lastSubmitEndMs = spanEnd;

    const pcm = this.cut(spanStart, spanEnd);
    if (pcm.length < SAMPLE_RATE * 0.2 || rms(pcm) < DROP_RMS) {
      this.trace(`skip-quiet turn=${turn.turnId} ${spanStart}..${spanEnd} rms=${rms(pcm).toFixed(4)} closing=${closing}`);
      if (closing) await this.closeOut(turn);
      return;
    }
    this.trace(`submit turn=${turn.turnId} ${spanStart}..${spanEnd} (${spanEnd - spanStart}ms) closing=${closing}`);

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
    const mapped = gated.map((ws) => {
      const relativeStart = ws.start || 0;
      const relativeEnd = ws.end || 0;
      const startMs = spanStart + relativeStart * 1000;
      // Provider timestamps are optional. A segment without a positive relative
      // span represents the submitted speech window and ends at its committed edge.
      const rawEndMs = relativeEnd > relativeStart
        ? spanStart + relativeEnd * 1000
        : publishEnd;
      const endMs = Math.min(publishEnd, rawEndMs) || publishEnd;
      return {
        text: ws.text.trim(),
        startMs,
        endMs,
        language: lang,
        relEnd: Math.max(0, (endMs - spanStart) / 1000),
        // Whisper's own word timestamps, already requested by the client. They are what lets a
        // sentence be attributed at the granularity the TRANSPORT changes at, instead of at the
        // granularity the decoder happened to choose.
        words: (ws as any).words as Array<{ word: string; start: number; end: number }> | undefined,
      };
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
      // Whisper invented this on non-speech. Reported, never dropped in silence.
      const rule = hallucinationRule(s.text);
      if (rule) {
        this.cb.onSuppressed?.({ text: s.text, startMs: s.startMs, endMs: s.endMs, rule });
        this.log(`[ChunkedTranscriber] suppressed (${rule}) ${Math.round(s.startMs)}..${Math.round(s.endMs)}: ${JSON.stringify(s.text)}`);
        this.suppressedCount++;
        return false;
      }
      return true;
    });
    if (mapped.length === 0) {
      if (closing) await this.closeOut(turn);
      return;
    }
    turn.lastVoicedWallMs = this.now();   // voiced update arrived — resets the TTL idle-finalize

    // TAIL DEDUP — the second half of the overlap fix. Advancing the window is the structural cure,
    // but a decoder handed a window that STARTS mid-phrase will still sometimes reach backwards for
    // context it can hear in the ring's pad. So a leading run of tokens that merely repeats the end
    // of what was just confirmed is dropped, bounded hard: only at the very start of the first
    // segment, only when the repeat is at least MIN tokens (so a genuine "да, да" survives), and
    // only while the window begins close behind the confirmed boundary.
    if (this.lastConfirmedTail && mapped.length > 0 && spanStart <= this.confirmedHighWaterMs + TAIL_DEDUP_REACH_MS) {
      const first = mapped[0];
      const tailTokens = tokens(this.lastConfirmedTail);
      const headTokens = tokens(first.text);
      let repeat = 0;
      for (let n = Math.min(tailTokens.length, headTokens.length); n >= TAIL_DEDUP_MIN_TOKENS; n--) {
        if (tailTokens.slice(-n).join(' ') === headTokens.slice(0, n).join(' ')) { repeat = n; break; }
      }
      if (repeat > 0) {
        const kept = first.text.split(/\s+/).slice(repeat).join(' ').trim();
        this.log(`[ChunkedTranscriber] dropped ${repeat} leading token(s) duplicating the confirmed tail`);
        if (kept) {
          first.text = kept;
          if (first.words && first.words.length > repeat) first.words = first.words.slice(repeat);
        } else {
          mapped.shift();
          if (mapped.length === 0) { if (closing) await this.closeOut(turn); return; }
        }
      }
    }

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
      // ATTRIBUTE BELOW THE TURN. Each confirmed segment is split along the transport's timeline at
      // its own word boundaries, so one turn may publish under more than one name — which is the
      // honest shape when the transport changed hands inside a sentence.
      const pieces: Array<{ name: string; seg: ChunkSegment }> = [];
      for (const s of mapped.slice(0, confirmCount)) {
        for (const part of this.splitByOwner(s, spanStart, name)) {
          pieces.push({
            name: part.name,
            seg: { text: part.text, startMs: part.startMs, endMs: part.endMs, language: part.language,
                   segmentId: `turn:${turn.turnId}:${turn.seq++}` },
          });
        }
      }
      const confirmed = pieces.map((p) => p.seg);
      // Group consecutive pieces by name so each speaker's run publishes as one bundle. The tail
      // rides with the LAST bundle: pending is a full-replace block per pass, so attaching it to an
      // earlier bundle would have the next one retract it.
      const groups: Array<{ name: string; segs: ChunkSegment[] }> = [];
      for (const p of pieces) {
        const g = groups[groups.length - 1];
        if (g && g.name === p.name) g.segs.push(p.seg);
        else groups.push({ name: p.name, segs: [p.seg] });
      }
      groups.forEach((g, i) => {
        const isLast = i === groups.length - 1;
        // ONE bundle: confirmed + surviving tail. Splitting them deletes the
        // client's pending block for seconds (the "vanishing transcript" bug).
        this.cb.publish(g.name, g.segs, isLast && !closing ? tail : []);
      });
      this.rememberPublishedSpeaker(groups[groups.length - 1]?.name ?? name, confirmed[confirmed.length - 1]?.endMs);
      turn.allConfirmed.push(...confirmed);
      // Track per-key so a later name change repaints these in place. Only the pieces published
      // under the TURN's own name may be repainted by a later rename — a piece the transport
      // assigned to the other speaker is not this turn's to rename.
      let cs = this.clusterSegments.get(turn.clusterId);
      if (!cs) { cs = []; this.clusterSegments.set(turn.clusterId, cs); }
      cs.push(...pieces.filter((p) => p.name === name).map((p) => p.seg));
      this.clusterName.set(turn.clusterId, name);
      // ADVANCE PAST WHAT WAS CONFIRMED, USING THE MOST PRECISE BOUNDARY AVAILABLE.
      //
      // The window used to advance to the whisper SEGMENT's end, which is routinely earlier than
      // the audio that produced it. The next window therefore re-fed the tail of already-final
      // speech, Whisper re-emitted it as the new row's prefix, and — because only DRAFTS can be
      // retracted and a confirmed row is immutable — the duplicate published. On m36 that is the
      // pair "Есть какие-то реал-тайм глюнки." followed by "Есть какие-то реал-тайм глюнки, но, в
      // основном, …": one pause, one resume, one sentence said twice.
      //
      // Word timestamps make the boundary exact, so use the last CONFIRMED WORD's end where the
      // decoder gave us one and fall back to the segment's end where it did not. Audio that has
      // produced a final is never transcribed again.
      const lastConfirmed = mapped[confirmCount - 1];
      const lastWord = lastConfirmed.words?.[lastConfirmed.words.length - 1];
      const boundaryRelSec = Math.max(lastConfirmed.relEnd, lastWord?.end ?? 0);
      turn.confirmedUpToMs = spanStart + boundaryRelSec * 1000;
      this.confirmedHighWaterMs = Math.max(this.confirmedHighWaterMs, turn.confirmedUpToMs);
      // Remember the tail of what just went out, so a re-emission of it can be recognised as one.
      this.lastConfirmedTail = confirmed.map((c) => c.text).join(' ').slice(-TAIL_DEDUP_CHARS);
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
    if (turn.pendingTail.length > 0) {
      // NEVER LOSE A TURN. The closing pass yielded nothing new, but drafts are still pending —
      // live-edge speech past the last committed boundary. Promote them to CONFIRMED before the
      // clearPending below retracts them; otherwise that speech is DELETED from the live view AND
      // the durable store (the pending-retract path). This covers the seq>0 case (a turn that
      // already confirmed earlier segments and closed with a dangling tail via one of submitTurn's
      // yielded-nothing early returns) — the previous guard only rescued a never-confirmed turn,
      // so a confirmed-then-dangling turn silently lost its trailing words. Continue the segment
      // numbering from turn.seq so the promoted ids never collide with turn:${id}:0..seq-1.
      const name = this.resolveName(turn);
      const promoted = turn.pendingTail.map((s, i) => ({ ...s, segmentId: `turn:${turn.turnId}:${turn.seq + i}` }));
      turn.seq += promoted.length;
      // publish(confirmed, []) republishes these as completed AND reconciles pending→[], so the
      // matching turn:${id}:p* drafts are retracted in the same breath (text moves p*→confirmed).
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
      turn.pendingTail = [];
      this.log(`[ChunkedTranscriber] turn ${turn.turnId}: promoted ${promoted.length} draft segment(s) on close`);
    }
    // The turn is over: its window can grow no further, so its name is final. Everything
    // after this point is the LATE path (unresolved queue / binder cluster vote), which
    // owns renames of closed turns; the open-turn re-resolution above must not run again.
    turn.nameLocked = true;
    if (turn.pendingName) this.cb.clearPending(turn.pendingName);
    // The transport spine remembers who just finished — the gap-reclaim rule in openTurnApply
    // needs it to refuse to carry one speaker's tail into the next speaker's window.
    if (turn.trackId || turn.contested) this.lastClosedTrackId = turn.trackId;
    this.lastClosedEndMs = Math.max(this.lastClosedEndMs, turn.t1);
    // A transport turn is NEVER queued for the late hint claim. Its name is the track's name and
    // arrives (or does not) through the TrackNamer's retroactive rename; letting it also sit in the
    // unresolved queue would re-open the per-hint race on the one path built to be free of it. A
    // contested turn is never claimed either: it has two speakers in it and no name is correct.
    if (turn.trackId || turn.contested) return;
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

  /** The one rendering rule, applied to every name this lane publishes whatever path produced it:
   *  if the roster showed this person, use the roster's casing. The tiles and the roster disagree
   *  about capitalisation ("leo" vs "Leo"), and on the full m30 tape that put ONE participant into
   *  the transcript under TWO labels — 34 rows as "Leo (Unverified)" and 18 as "leo (Unverified)",
   *  which reads as two people. Never invents a capitalisation nobody rendered. */
  private render(name: string): string {
    return this.trackNamer.canonicalCase(name);
  }

  /**
   * Split ONE transcribed segment into per-speaker pieces along the transport's timeline.
   *
   * Attribution used to stop at the turn: a turn got one owner and every word in it inherited that
   * owner, so a span the transport could not assign published an ENTIRE SENTENCE as "Speaker". On
   * the m34 tape that was 12 rows — and 11 of them were rows where the tiles knew perfectly well
   * who was speaking, because the transport's "both audible" is a much blunter statement than the
   * meeting actually made. Measured on that tape, two sources are genuinely audible together for
   * 5.0% of the meeting, in stretches whose median length is one second; a whole sentence is never
   * that.
   *
   * So each WORD is asked the question separately, and consecutive words with the same answer stay
   * together. A word inside one source's run takes that source. A word in a contested stretch is
   * offered to the tiles — the second, independent signal — and only when they name exactly one
   * person, and that person already owns one of the two contesting tracks, does it take a name;
   * that constraint is what stops this from being the flickery per-turn DOM race all over again.
   * Anything still undecided stays "Speaker", which is now a sliver rather than a sentence.
   */
  private splitByOwner(seg: { text: string; startMs: number; endMs: number; language: string; words?: Array<{ word: string; start: number; end: number }> },
                       spanStart: number, fallbackName: string): Array<{ name: string; text: string; startMs: number; endMs: number; language: string }> {
    const src = this.csrcSource;
    if (!src || this.authoritative !== 'csrc') return [{ name: fallbackName, ...seg }];
    const words = seg.words?.filter((w) => typeof w.start === 'number' && (w.word ?? '').trim());

    const nameAt = (tMs: number): string => {
      const o = src.ownerAt(tMs);
      if (o.trackId) return this.trackNamer.labelFor(o.trackId);
      if (o.contested) {
        // The transport says two sources are audible — but on the staging Jacob call that claim was
        // mostly the mixer HOLDING a source open, not two people speaking: both tracks read active
        // across 2% of the meeting yet produced 22% of the rows, all of them refused, all of them
        // in the middle of one man's continuous speech. Asking whether a tile was LIT could not
        // separate them, because a lit interval survives silence by construction.
        //
        // So ask which tile was being RE-ASSERTED instead. A hint event is emitted when the watcher
        // sees the outline move; the speaker re-emits one every couple of seconds while the held
        // source emits nothing. A strict majority within a second either side decides it, and two
        // people genuinely talking at once still tie and still resolve to nobody.
        // THE TILES ONLY GET A VOTE IF THEY CAN TESTIFY ABOUT EVERYONE IN THE MIX. On the m30 tape
        // the outline lights for exactly ONE of the two participants for the whole meeting, so a
        // tile majority there says "leo" no matter who is speaking — one-sided by construction, and
        // reading the other man's silence as absence put nine wrong names into the transcript the
        // first time this rule ran. A signal that has never once named someone cannot be evidence
        // that they are not the one talking.
        const audible = src.tracksAudibleAt(tMs);
        const everyoneTestified = audible.every((t) => {
          const n = this.trackNamer.nameFor(t);
          return n !== null && this.trackNamer.hintEvidenceFor(n) >= CONTESTED_MIN_TILE_EVIDENCE;
        });
        if (everyoneTestified) {
          const lit = this.trackNamer.hintLeaderAt(tMs, CONTESTED_HINT_TOLERANCE_MS)
            ?? this.trackNamer.litNameAt(tMs);
          if (lit) {
            for (const t of audible) {
              if (this.trackNamer.nameFor(t) === this.trackNamer.canonicalCase(lit)) return this.trackNamer.labelFor(t);
            }
          }
        }
      }
      return PROVISIONAL_SPEAKER;
    };

    // WITHOUT word timestamps the segment is still a span, and the span still has an owner. Falling
    // back to the turn's own name here meant a decoder that returns no words could never resolve a
    // contested span at all — every one of them would publish as "Speaker" no matter how clearly the
    // tiles said who was talking. Ask once, for the whole segment, at its midpoint.
    if (!words || words.length === 0) {
      return [{ name: nameAt((seg.startMs + seg.endMs) / 2), ...seg }];
    }

    const out: Array<{ name: string; text: string; startMs: number; endMs: number; language: string }> = [];
    for (const w of words) {
      const t0 = spanStart + w.start * 1000;
      const t1 = spanStart + (w.end ?? w.start) * 1000;
      const name = nameAt((t0 + t1) / 2);
      const last = out[out.length - 1];
      // Concatenate the tokens VERBATIM. Whisper's word tokens carry their own leading space, and a
      // suffix like "-то" carries none precisely because it must not have one — re-joining trimmed
      // tokens with a space produced "что -то" in the founder's transcript. The decoder already
      // knows where the spaces go; the only trimming happens at a piece's outer edges.
      if (last && last.name === name) {
        last.text += w.word;
        last.endMs = Math.max(last.endMs, t1);
      } else {
        out.push({ name, text: w.word, startMs: t0, endMs: Math.max(t1, t0 + 1), language: seg.language });
      }
    }
    for (const piece of out) piece.text = piece.text.trim();
    const kept = out.filter((piece) => piece.text.length > 0);
    if (kept.length === 0) return [{ name: fallbackName, ...seg }];
    out.length = 0; out.push(...kept);
    // Clip to the segment's own bounds so nothing published moves outside the audio it came from.
    out[0].startMs = Math.min(out[0].startMs, seg.startMs);
    out[out.length - 1].endMs = Math.max(out[out.length - 1].endMs, seg.endMs);
    return out;
  }

  private resolveName(turn: Turn): string {
    // ── THE TRANSPORT PATH ────────────────────────────────────────────────────────────────────
    // A turn that carries a trackId does not go through ANY of the machinery below. That machinery
    // exists because a pyannote turn has no identity of its own, so a name had to be raced for
    // per turn — which is where both of the 2026-08-10 misattribution bugs lived (the per-turn
    // claim and the open-turn re-resolution). A transport turn already knows WHICH SPEAKER it is;
    // the only open question is that speaker's name, and that is answered once per meeting by the
    // TrackNamer against evidence, not once per turn against timing. The old paths are untouched
    // and still carry the pyannote lane.
    if (turn.contested || (turn.spanTracks && !turn.trackId)) {
      // ORPHAN CONTAINMENT. The transport declined to own this span. Before publishing it as
      // "Speaker", ask whether the span was ambiguous or merely unowned: exactly one candidate
      // track — audible somewhere inside it, or the turn immediately before it — means there was
      // never a second person it could have been. Two candidates is real crosstalk and it stays
      // unattributed, which on the m30 fixture is all three of them.
      const contained = this.containingTrack(turn);
      if (contained) {
        turn.trackId = contained;
        const label = this.trackNamer.labelFor(contained);
        turn.resolvedName = label;
        let keys = this.trackClusters.get(contained);
        if (!keys) { keys = new Set(); this.trackClusters.set(contained, keys); }
        keys.add(turn.clusterId);   // so a later name repaints this span too
        this.orphanContained++;
        this.log(`[ChunkedTranscriber] orphan ${turn.clusterId} contained by track ${contained} → "${label}"`);
        return label;
      }
      return turn.clusterId;                            // two voices in one mix — nobody's name
    }
    if (turn.trackId) {
      const label = this.trackNamer.labelFor(turn.trackId);
      turn.resolvedName = label;
      return label;
    }
    // STICKY-UNTIL-CLOSE ATTRIBUTION. A turn's name is locked when the turn CLOSES, not on
    // the first tick that produced any name at all.
    //
    // Why this changed (m24, the first real Teams tape). The old rule was `if
    // (turn.resolvedName) return turn.resolvedName` — the very first submit tick, whose
    // commit window is a fraction of a second, decided the whole turn. Whoever's tile
    // happened to be lit at the turn's onset owned every later segment of it. On Teams the
    // tile lights on NOISE, so the other participant TYPING at the moment a turn began
    // stamped his name on the entire utterance. Measured on the tape: over the ground-truth
    // window 1786397358.0–1786397406.5 (Dmitry describing the teams-capture module) the
    // hints name Dmitry for 145.4s of lit time against Jacob's 47.2s — better than 3:1 —
    // and the transcript still said Jacob for all of it. The evidence was there; the lock
    // was taken before it arrived.
    //
    // So while the turn is OPEN we keep re-resolving over the GROWN window and let a
    // challenger take the turn only if it beats the incumbent's confidence by
    // REATTRIBUTE_MARGIN. That is strictly MORE evidence, never invented evidence: an
    // unresolvable turn still returns its cluster id and stays "Speaker". Every flip
    // repaints the already-published segments through the same rename path a late hint
    // uses, so nothing is orphaned under the old name.
    if (turn.resolvedName && turn.nameLocked) return turn.resolvedName;
    const commit = { clusterId: turn.clusterId, tStartMs: turn.t0, tEndMs: turn.t1 };
    // recordVote:false — we only commit a vote once the result survives the
    // short-UI-switch guard below, so a held-provisional bad hint never votes.
    const r = this.binder.resolve(commit, { recordVote: false });
    if (r.source === 'provisional-cluster-id') return turn.resolvedName ?? r.speakerName;
    if (this.shouldDeferShortUiSwitch(turn, r.speakerName, r.source)) {
      // A brief isolated tile flip to a NEW name right after a different speaker:
      // hold provisional and block that name from a later claim/rename.
      if (!turn.blockedNames) turn.blockedNames = new Set();
      turn.blockedNames.add(r.speakerName);
      this.log(`[ChunkedTranscriber] short UI switch held provisional ${turn.clusterId}; speaker=${r.speakerName}`);
      return turn.resolvedName ?? turn.clusterId;
    }
    if (turn.blockedNames?.has(r.speakerName)) return turn.resolvedName ?? turn.clusterId;
    if (!turn.resolvedName) {
      this.binder.recordClusterVote(turn.clusterId, r.speakerName);
      turn.resolvedName = this.render(r.speakerName);
      turn.resolvedConfidence = r.confidence;
      return turn.resolvedName;
    }
    if (r.speakerName === turn.resolvedName) {
      turn.resolvedConfidence = r.confidence;
      return turn.resolvedName;
    }
    if (r.source === 'window-match' && r.confidence >= REATTRIBUTE_MIN_CONFIDENCE) {
      this.log(`[ChunkedTranscriber] re-attributed ${turn.clusterId}: "${turn.resolvedName}" → "${r.speakerName}" (conf ${r.confidence.toFixed(2)}) over the grown turn window`);
      this.binder.recordClusterVote(turn.clusterId, r.speakerName);
      turn.resolvedName = this.render(r.speakerName);
      turn.resolvedConfidence = r.confidence;
      this.claimTurn(turn.clusterId, turn.resolvedName);   // repaint what this turn already published
      return turn.resolvedName;
    }
    return turn.resolvedName;
  }

  /** The ONE track an orphan span can only have belonged to, or null. Candidates are every track
   *  audible inside the span plus — when the orphan opens right after it — the previous turn's
   *  track. More than one candidate is a refusal, always. */
  private containingTrack(turn: Turn): string | null {
    // resolveName runs on every submission, so count the ORPHAN once rather than once per pass.
    if (!turn.orphanCounted) { turn.orphanCounted = true; this.orphanTurns++; }
    // UNKNOWN IS NOT EMPTY. The spine reports a turn's track membership when it closes it — but the
    // TTL finalize can close a turn first, and then that report never lands. Treating the missing
    // report as "no other track was audible" let the adjacency rule name a 5.4-second CROSSTALK
    // span on the m30 tape after both speakers had been talking through the whole of it. If the
    // spine never told us, we do not know, and we do not guess.
    if (turn.spanTracks === undefined) return null;
    const candidates = new Set<string>(turn.spanTracks);
    if (candidates.size === 0
      && this.lastClosedTrackId
      && turn.t0 - this.lastClosedEndMs <= ORPHAN_ADJACENT_MS
      && turn.t0 >= this.lastClosedEndMs) {
      candidates.add(this.lastClosedTrackId);
    }
    return candidates.size === 1 ? [...candidates][0] : null;
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
