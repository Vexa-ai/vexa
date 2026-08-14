/**
 * Pipeline adapter (2b) — the capture → lane → STT → transcript.v1 engine, behind the
 * orchestrator's Pipeline port.
 *
 * The bot rewires NOTHING about transcription topology: the per-channel lane (turn gating,
 * LocalAgreement confirmation, glow→channel naming) lives in @vexa/gmeet-pipeline, the mixed
 * lane (pyannote cut + hints naming, no diarizer) in @vexa/mixed-pipeline, and the stt.v1
 * round-trip in @vexa/transcribe-whisper. This adapter only:
 *   1. picks the lane on inv.platform (google_meet → native per-channel; teams → CSRC virtual
 *      channels over the shared GMeet window; zoom/jitsi → legacy mixed segmentation),
 *   2. injects the stt port (TranscriptionClient.transcribe) + a TranscriptSink, and
 *   3. RECONCILES the lane's TranscriptSink (segment/draft/finalize — owned by the lane's
 *      transcript.v1 contract) onto the bot's injected TranscriptSink.publish(segment) port.
 *
 * The capture BRIDGE (browser page-side capture → feedAudio) lives in capture-bridge.ts and is
 * L4-gated; this file is the offline-provable lane composition (L2/L3 via pipeline.test.ts:
 * synthetic PCM → mock transcribe → assert publish got transcript.v1-valid segments).
 *
 * Ported from services/vexa-bot_new/src/pipeline/gmeet-pipeline.ts (the lane wrapper) onto the
 * v0.12 ports; Teams now wires @vexa/mixed-pipeline's CSRC/GMeet lane while Zoom/Jitsi retain
 * ChunkedTranscriber.
 */
import {
  createGmeetPipeline,
  type TranscriptSink as LaneTranscriptSink,
  type TranscriptSegment as LaneSegment,
  type SpeakerStreamManagerConfig,
} from '@vexa/gmeet-pipeline';
import {
  ChunkedTranscriber,
  TeamsCsrcGmeetPipeline,
  type ChunkSegment,
  type ChunkedTranscriberCallbacks,
  type HintKind,
  type TeamsCsrcGmeetPipelineOptions,
  type TeamsCsrcTranscriptSegment,
  type TransportEvent,
  type TurnSourceObservation,
} from '@vexa/mixed-pipeline';
import { TranscriptionClient, type TranscriptionResult } from '@vexa/transcribe-whisper';
import { isMixedLanePlatform, type Invocation, type Platform } from './config.js';
import type { TranscriptSegment } from './contracts.js';
import type { Pipeline, TranscriptSink } from './ports.js';

/** stt.v1 round-trip — real adapter = TranscriptionClient.transcribe; L2/L3 = a mock. The
 *  lane bakes language/prompt at the call site, so the closure carries the configured language. */
export type Transcribe = (pcm: Float32Array, prompt?: string) => Promise<TranscriptionResult>;

/** Each platform's TRUE hint kind — the binder's lag correction is per-kind
 *  (cluster-name-binder KIND_LAG_MS), so the label must survive the bot's wiring:
 *  Teams' voice-level outline is 'dom-outline'; Zoom's active-speaker DOM poll and
 *  jitsi's dominant-speaker signal ride 'dom-active'. Bound once at wiring time —
 *  the page-side watcher and the transcriber never renegotiate it. */
export function hintKindForPlatform(platform: Platform | string): HintKind {
  return platform === 'teams' ? 'dom-outline' : 'dom-active';
}

/** Cumulative hint-hop counters (the C1 instrument): how many hints crossed into the
 *  pipeline, and their instantaneous binder outcome. Printed on the bridge's periodic
 *  counter line so a name lost between the page and the transcript names its hop. */
export interface HintCounters {
  received: number;
  matched: number;
  missed: number;
}

/** The mixed-lane transcriber seam — the REAL ChunkedTranscriber in production,
 *  injectable so an offline test observes exactly what reaches the transcriber
 *  (name, KIND, tMs) without the pyannote model load. */
export type MixedTranscriber =
  Pick<ChunkedTranscriber, 'feedAudio' | 'recordHint' | 'dispose'>
  & Partial<Pick<ChunkedTranscriber, 'recordTransportEvent' | 'recordCaption' | 'recordRosterName' | 'recordRosterCoverage'>>;
export type MixedTranscriberFactory = (cb: ChunkedTranscriberCallbacks) => Promise<MixedTranscriber>;

/** Teams is still a mixed CAPTURE lane, but its transcription lane is a set of CSRC-owned
 * virtual channels feeding the shared GMeet window. Keep the construction seam injectable so the
 * bot adapter is provable without timers or Whisper while the real class stays independently
 * covered in @vexa/mixed-pipeline. */
export type TeamsTranscriber = Pick<
  TeamsCsrcGmeetPipeline,
  | 'feedMixedAudio'
  | 'recordTransportEvent'
  | 'recordHint'
  | 'recordCaption'
  | 'recordRosterName'
  | 'recordRosterCoverage'
  | 'dispose'
>;
export type TeamsTranscriberFactory = (options: TeamsCsrcGmeetPipelineOptions) => TeamsTranscriber;

/** The Pipeline port extended with the capture entry the bridge pumps frames into. The
 *  orchestrator only sees start/stop; the capture bridge holds the BotPipeline to feedAudio. */
export interface BotPipeline extends Pipeline {
  /** One gmeet capture.v1 frame: CHANNEL index + glow NAME (undefined ⇒ no single glow now). */
  feedAudio(channel: number, glowName: string | undefined, pcm: Float32Array, tsMs: number): void;
  /** One mixed (Zoom/Teams) capture.v1 frame: a single mixed PCM stream, named downstream. */
  feedMixedAudio(pcm: Float32Array, tsMs: number): void;
  /** A mixed-lane "who is lit" hint (platform active-speaker), windowed by the namer.
   *  CLOCK CONTRACT: tMs MUST be epoch ms — the same domain as feedMixedAudio's tsMs —
   *  or no hint window can ever overlap a speech turn (the bridge guards this). The
   *  platform's hint KIND is bound at wiring time (hintKindForPlatform), not per call. */
  recordHint(name: string, tMs: number, isEnd?: boolean): void;
  /** A mixed-lane TRANSPORT edge: the RTP mixer said a contributing source became (in)audible.
   *  Deliberately NOT recordHint — it carries no name and must never reach the name binder. It is
   *  the turn SPINE: `csrc` is a stable per-meeting identity, `tMs` is epoch (the bridge guards it). */
  recordTransportEvent?(ev: TransportEvent): void;
  /** A mixed-lane caption AUTHOR (Teams' own ASR) — name evidence for a transport track, never a
   *  turn edge. Absent on platforms/tenants with no captions. */
  recordCaptionName?(name: string, tMs: number): void;
  /** A mixed-lane ROSTER display name — who is in the meeting. Never a turn edge and never a hint:
   *  a roster name says nothing about when anyone spoke. It is the only way to name a participant
   *  whose tile never lights, which on the m30 fixture was one of the two people in the room. */
  recordRosterName?(name: string, tMs?: number): void;
  /** How much of the roster the producer could read (seen vs named) — the completeness premise
   *  the namer's elimination rule depends on. */
  recordRosterCoverage?(named: number, participants: number, tMs?: number): void;
  /** Mixed lane only: the cumulative hint-hop counters (undefined on the gmeet lane). */
  readonly hintCounters?: HintCounters;
}

/** The lane segments are the SEALED transcript.v1 view — structurally identical to the bot's
 *  contracts.ts TranscriptSegment (same SSOT schema). Map defensively so a future drift in
 *  either view is a compile error here, not a silent wire mismatch. */
/** The lanes time every segment on the WALL CLOCK: `start`/`end` are epoch seconds (windowStartMs/1000).
 *  Stamp the canonical ISO `absolute_start_time` HERE, at the single producer chokepoint, so every
 *  consumer (dashboard renderer, meeting-api transcript read) uses it DIRECTLY. Downstream must never
 *  re-derive it from `start`: a relative-offset assumption (meeting_start + start) put timestamps ~56
 *  years in the future. We own the producer, so we emit the truth once. */
const isoFromEpochSeconds = (s: number | undefined): string | undefined =>
  typeof s === "number" && Number.isFinite(s) && s > 0 ? new Date(s * 1000).toISOString() : undefined;

function toBotSegment(seg: LaneSegment): TranscriptSegment {
  return {
    segment_id: seg.segment_id,
    // The gmeet lane publishes the same 'Speaker' placeholder for an unnamed channel, and it
    // reaches the same dashboard — so it gets the same treatment. One rule, both lanes.
    speaker: displaySpeaker(seg.speaker ?? ''),
    speaker_key: seg.speaker_key,
    text: seg.text,
    start: seg.start,
    end: seg.end,
    language: seg.language ?? undefined,
    completed: seg.completed,
    absolute_start_time: seg.absolute_start_time ?? isoFromEpochSeconds(seg.start),
    absolute_end_time: seg.absolute_end_time ?? isoFromEpochSeconds(seg.end),
    source: seg.source,
    confidence: seg.confidence,
    words: seg.words,
  };
}

/**
 * The sink ADAPTER — the load-bearing reconciliation. The lane emits via `segment` (confirmed),
 * `draft` (live partial, completed:false), `finalize` (session end); the bot's port is a single
 * `publish(segment)`. We forward BOTH `segment` and `draft` to publish (the bot's transcript.v1
 * egress carries `completed` to distinguish confirmed from draft) and treat `finalize` as a
 * no-op at this seam (the bot signals end-of-session via lifecycle.v1, not the transcript stream).
 * publish() is async; the lane's sink methods are sync fire-and-forget, so we swallow + log a
 * rejection rather than letting it escape the lane's emit path.
 */
function laneSink(publish: TranscriptSink['publish'], onError?: (e: unknown) => void): LaneTranscriptSink {
  const forward = (seg: LaneSegment): void => {
    void publish(toBotSegment(seg)).catch((e) => {
      (onError ?? ((err) => console.error(`[bot] pipeline: transcript publish rejected: ${String(err)}`)))(e);
    });
  };
  return {
    segment: forward,
    draft: forward,
    finalize() { /* session end is a lifecycle.v1 concern, not a transcript.v1 segment */ },
  };
}

/** A ChunkSegment (mixed lane) → the bot's transcript.v1 segment. The mixed lane publishes
 *  per-speaker bundles (confirmed + pending); we map each to a segment carrying `speaker` and
 *  `completed`. Audio-time is ms there → seconds here (transcript.v1 timing is seconds).
 *
 *  Stamp the canonical ISO `absolute_start_time` HERE, exactly as `toBotSegment` does for the
 *  gmeet lane — the mixed lane's `startMs`/`endMs` are epoch milliseconds (wall clock), so
 *  `startMs/1000` is epoch seconds. Without it the live `:mutable` bundle carries a null
 *  absolute_start_time and the dashboard renderer SKIPS every pending draft (it keys on absolute
 *  time), so Teams/Zoom transcripts only appeared after a reload (the REST read re-derives it). */
/**
 * Labels that name NOBODY, in every spelling the lane produces: the provisional segmentation id,
 * the word the lane publishes it as, and the stable letter a distinct-but-unnamed transport track
 * carries. All three are INTERNAL — they are how the lane talks to itself about a refusal.
 */
const UNATTRIBUTED_LABEL = /^(?:seg_\d+|Speaker|Speaker [A-Z]+)$/;

/**
 * What a viewer sees when we could not name the speaker: NOTHING.
 *
 * Founder ruling from the rc.3 witness call. A row labelled "Speaker" advertises a failed claim —
 * it is the product telling the customer, in the customer's own transcript, that it tried and
 * missed. A blank simply reads as continuation of the passage. The refusal has not been hidden: the
 * track identity survives in `speaker_key`, and WHY the lane refused lives in the observations
 * sidecar beside the tape. What changes is that the failure stops being addressed to the customer.
 *
 * This is the one place the mapping happens. The lane keeps its internal labels, so the replay
 * harness and every score built on it keep counting refusals exactly as before rather than going
 * blind the moment the display string changed.
 */
function displaySpeaker(internal: string): string {
  return UNATTRIBUTED_LABEL.test(internal) ? '' : internal;
}

function chunkToBotSegment(speaker: string, c: ChunkSegment, completed: boolean): TranscriptSegment {
  return {
    segment_id: c.segmentId,
    speaker: displaySpeaker(speaker),
    speaker_key: c.segmentId,
    text: c.text,
    start: c.startMs / 1000,
    end: c.endMs / 1000,
    language: c.language,
    completed,
    absolute_start_time: isoFromEpochSeconds(c.startMs / 1000),
    absolute_end_time: isoFromEpochSeconds(c.endMs / 1000),
    source: 'merged',
  };
}

/** A CSRC-owned Teams row -> transcript.v1. `speaker_key` is the stable transport identity,
 * deliberately independent of the changing Whisper window/segment id, so a late proven name
 * repaints ordinary upserts without splitting one human into multiple transcript speakers. */
function teamsToBotSegment(segment: TeamsCsrcTranscriptSegment): TranscriptSegment {
  return {
    segment_id: segment.segmentId,
    speaker: displaySpeaker(segment.speaker),
    speaker_key: `csrc:${segment.csrc}`,
    text: segment.text,
    start: segment.startMs / 1000,
    end: segment.endMs / 1000,
    language: segment.language,
    completed: segment.completed,
    absolute_start_time: isoFromEpochSeconds(segment.startMs / 1000),
    absolute_end_time: isoFromEpochSeconds(segment.endMs / 1000),
    source: 'merged',
  };
}

/** Build the gmeet (per-channel) BotPipeline. The lane is lazy — it begins on the first fed
 *  frame (post-admission), so start() is a no-op and stop() disposes (flush every turn → finalize). */
function createGmeetBotPipeline(
  transcribe: Transcribe,
  sink: TranscriptSink,
  config?: SpeakerStreamManagerConfig,
  onError?: (e: unknown) => void,
): BotPipeline {
  const lane = createGmeetPipeline({ transcribe, sink: laneSink(sink.publish, onError), config, onError });
  return {
    async start() { /* lane is lazy — begins on the first fed frame */ },
    async stop() { await lane.dispose(); },
    feedAudio: (channel, glowName, pcm, tsMs) => lane.feedAudio(channel, glowName, pcm, tsMs),
    feedMixedAudio() { /* not the gmeet lane */ },
    recordHint() { /* not the gmeet lane */ },
  };
}

/** Build the Teams-only CSRC virtual-channel lane. The shared GMeet-compatible buffer owns
 * cadence, prompt feedback, LocalAgreement and terminal fallback. This adapter owns only the bot
 * ports: mixed PCM/identity evidence in, transcript.v1 publish/retract out. */
function createTeamsBotPipeline(
  transcribe: Transcribe,
  sink: TranscriptSink,
  onError?: (e: unknown) => void,
  createTranscriber: TeamsTranscriberFactory = (options) => new TeamsCsrcGmeetPipeline(options),
  onObservation?: (source: string, obs: Record<string, unknown>, tMs?: number) => void,
  selfName?: string,
): BotPipeline {
  const hintCounters: HintCounters = { received: 0, matched: 0, missed: 0 };
  const reportError = onError ?? ((error: unknown) => console.error(`[bot] pipeline(teams-csrc): ${String(error)}`));

  const transcriber = createTranscriber({
    transcribe: (pcm, prompt) => transcribe(pcm, prompt),
    selfName,
    onSegment: (segment) => {
      const task = !segment.completed && !segment.text.trim()
        ? sink.retract?.([segment.segmentId])
        : sink.publish(teamsToBotSegment(segment));
      void task?.catch(reportError);
    },
    onRejectedOwnership: (segment, intervals) => {
      try {
        onObservation?.('teams-csrc-ownership-rejected', { segment, intervals }, segment.endMs);
      } catch { /* diagnostics never break the transcript lane */ }
    },
    onError: reportError,
  });

  return {
    async start() { /* lane is ready synchronously and begins on its first routed frame */ },
    async stop() { await transcriber.dispose(); },
    feedAudio() { /* Teams supplies one decoded mixed stream */ },
    feedMixedAudio: (pcm, tsMs) => transcriber.feedMixedAudio(pcm, tsMs),
    recordHint: (name, tMs, isEnd) => {
      hintCounters.received++;
      transcriber.recordHint(name, tMs, isEnd);
    },
    recordTransportEvent: (event) => transcriber.recordTransportEvent(event),
    recordCaptionName: (name, tMs) => transcriber.recordCaption(name, tMs),
    recordRosterName: (name, tMs) => transcriber.recordRosterName(name, tMs),
    recordRosterCoverage: (named, participants, tMs) => transcriber.recordRosterCoverage(named, participants, tMs),
    hintCounters,
  };
}

/** Build the legacy mixed (Zoom/Jitsi) BotPipeline over @vexa/mixed-pipeline's ChunkedTranscriber.
 *  ChunkedTranscriber.create is async (it constructs the pyannote segmenter), so the transcriber
 *  is built on the first start()/feed; we lazily await it and queue nothing before it's ready
 *  (frames before create resolves are dropped — the model needs seconds to lock on regardless). */
function createMixedBotPipeline(
  transcribe: Transcribe,
  sink: TranscriptSink,
  hintKind: HintKind,
  language?: string,
  onError?: (e: unknown) => void,
  createTranscriber: MixedTranscriberFactory = (cb) => ChunkedTranscriber.create(cb),
  onObservation?: (source: string, obs: Record<string, unknown>, tMs?: number) => void,
  selfName?: string,
): BotPipeline {
  let transcriber: MixedTranscriber | null = null;
  let creating: Promise<MixedTranscriber> | null = null;
  const hintCounters: HintCounters = { received: 0, matched: 0, missed: 0 };

  const publish = (speaker: string, segs: ChunkSegment[], completed: boolean): void => {
    for (const c of segs) {
      void sink.publish(chunkToBotSegment(speaker, c, completed)).catch((e) => {
        (onError ?? ((err) => console.error(`[bot] pipeline(mixed): publish rejected: ${String(err)}`)))(e);
      });
    }
  };

  // The mixed lane's pending tail is a FULL-REPLACE block: the ChunkedTranscriber republishes the whole
  // surviving tail each pass, shrinking it as segments confirm (under NEW `turn:N:{seq}` ids) and emptying
  // it when the turn closes. The bot's egress is an append-only stream the terminal upserts by id, so a
  // pending id that DROPS OUT of the block is never removed → it lingers as a stale "unattached" draft
  // (and an over-read past the turn boundary re-appears when the next turn transcribes the same audio).
  // Diff the pending id set each pass and RETRACT the departed ids so the terminal + durable store drop
  // them, leaving only the clean confirmed segments. One turn is open at a time (the pump serializes), so
  // a single set tracks the whole lane.
  let pendingIds = new Set<string>();
  const reconcilePending = (pending: ChunkSegment[]): void => {
    const next = new Set(pending.map((c) => c.segmentId));
    const retracted: string[] = [];
    for (const id of pendingIds) if (!next.has(id)) retracted.push(id);
    pendingIds = next;
    if (retracted.length > 0 && sink.retract) {
      void sink.retract(retracted).catch((e) => {
        (onError ?? ((err) => console.error(`[bot] pipeline(mixed): retract rejected: ${String(err)}`)))(e);
      });
    }
  };

  const ensure = (): Promise<MixedTranscriber> => {
    if (transcriber) return Promise.resolve(transcriber);
    if (!creating) {
      creating = createTranscriber({
        transcribe,
        // ONE atomic bundle: newly-confirmed (persisted) + the surviving pending tail. Reconcile the
        // pending block FIRST so a draft that just confirmed (or dropped out) is retracted, not orphaned.
        publish: (speaker, confirmed, pending) => { publish(speaker, confirmed, true); reconcilePending(pending); publish(speaker, pending, false); },
        publishPending: (speaker, segments) => { reconcilePending(segments); publish(speaker, segments, false); },
        // The turn's pending is gone (moved to another name / tail emptied / turn closed) → retract it.
        clearPending: () => { reconcilePending([]); },
        rename: (_oldSpeaker, newSpeaker, segments) => publish(newSpeaker, segments, true),
        language,
        onError,
        // C1 hop 4: the binder's instantaneous verdict per hint — a hint with no
        // overlapping turn increments `missed` (loudly, on the periodic counter line).
        onHintOutcome: (o) => { if (o.outcome === 'matched') hintCounters.matched++; else hintCounters.missed++; },
        // ARM THE TRANSPORT SPINE. 'auto', never 'csrc': a client that does not mix server-side
        // emits no contributing sources at all, and a spine with nothing to say would produce an
        // empty transcript rather than a degraded one. So pyannote carries the meeting until the
        // transport speaks, the transport takes over when it does, and it hands back — mid-meeting,
        // on the same ring — if it goes silent under continuing speech.
        turnSource: 'auto',
        selfName,
        onObservation: (o: TurnSourceObservation) => {
          // A transcript cannot say which spine produced it, so the switch is DATA beside the
          // tape, not just a log line — otherwise a replay can never attribute what it measures.
          console.log(`[bot] pipeline(mixed): turn spine ${o.from} → ${o.to} (${o.reason})`);
          try { onObservation?.('mixed-turn-source', { ...o }, o.tMs); } catch { /* diagnostics never break the lane */ }
        },
      }).then((t) => { transcriber = t; return t; })
        // #593: DON'T cache a rejected create promise. The mixed lane's create() loads the pyannote
        // model (from_pretrained) — if that rejects (empty HF cache, no egress), leaving `creating`
        // as a stuck rejected promise makes every later start() reject too, so the non-fatal retry
        // in createLivePipeline could never succeed. Clear it on failure so a retry re-attempts the load.
        .catch((e) => { creating = null; throw e; });
    }
    return creating;
  };

  return {
    async start() { await ensure(); },
    async stop() { if (transcriber) await transcriber.dispose(); },
    feedAudio() { /* not the mixed lane (mixed has no per-channel glow) */ },
    feedMixedAudio: (pcm, tsMs) => { transcriber?.feedAudio(pcm, tsMs); },
    // C1 hop 3 + C2: count the arrival, forward under the platform's TRUE kind.
    recordHint: (name, tMs, isEnd) => { hintCounters.received++; transcriber?.recordHint(name, hintKind, tMs, isEnd); },
    recordTransportEvent: (ev) => { transcriber?.recordTransportEvent?.(ev); },
    recordCaptionName: (name, tMs) => { transcriber?.recordCaption?.(name, tMs); },
    recordRosterName: (name, tMs) => { transcriber?.recordRosterName?.(name, tMs); },
    recordRosterCoverage: (named, participants, tMs) => { transcriber?.recordRosterCoverage?.(named, participants, tMs); },
    hintCounters,
  };
}

/** Build the real STT transcribe closure from invocation.v1 — language baked into the call so
 *  the lane never knows about config. transcribeEnabled=false ⇒ a no-op transcribe (the engine
 *  still runs turn gating but emits empty text; recording-only meetings need no STT). */
export function createTranscribe(inv: Invocation): Transcribe {
  if (inv.transcribeEnabled === false || !inv.transcriptionServiceUrl) {
    return async () => ({ text: '', language: inv.language ?? 'en', duration: 0, segments: [] });
  }
  const client = new TranscriptionClient({
    serviceUrl: inv.transcriptionServiceUrl,
    apiToken: inv.transcriptionServiceToken,
    model: inv.transcriptionModel ?? undefined,
  });
  const language = inv.language ?? undefined;
  return (pcm, prompt) => client.transcribe(pcm, language, prompt);
}

/**
 * The composition-root factory: pick the transcription lane on platform and wire STT + sink.
 * Google Meet uses native per-channel capture, Teams uses CSRC virtual channels over the shared
 * GMeet window, and Zoom/Jitsi keep the legacy mixed segmenter.
 */
export function createBotPipeline(
  inv: Invocation,
  sink: TranscriptSink,
  opts: {
    transcribe?: Transcribe;
    config?: SpeakerStreamManagerConfig;
    onError?: (e: unknown) => void;
    /** Mixed-lane transcriber seam — the real ChunkedTranscriber unless a test injects
     *  an observer (pins what actually reaches the transcriber: name, kind, tMs). */
    createMixedTranscriber?: MixedTranscriberFactory;
    /** Teams-only CSRC/GMeet lane construction seam. It is separate from the legacy mixed factory
     * so a test cannot accidentally prove Pyannote while production selects virtual channels. */
    createTeamsTranscriber?: TeamsTranscriberFactory;
    /** Where the lane's own typed observations go (the turn-spine switch). Wired at the
     *  composition root to the capture-signal recorder's observations sidecar. */
    onObservation?: (source: string, obs: Record<string, unknown>, tMs?: number) => void;
  } = {},
): BotPipeline {
  const transcribe = opts.transcribe ?? createTranscribe(inv);
  if (inv.platform === 'teams') {
    return createTeamsBotPipeline(
      transcribe, sink, opts.onError, opts.createTeamsTranscriber, opts.onObservation, inv.botName,
    );
  }
  if (isMixedLanePlatform(inv.platform)) {
    return createMixedBotPipeline(
      transcribe, sink, hintKindForPlatform(inv.platform),
      inv.language ?? undefined, opts.onError, opts.createMixedTranscriber, opts.onObservation,
      inv.botName,
    );
  }
  return createGmeetBotPipeline(transcribe, sink, opts.config, opts.onError);
}

/** The post-admission subsystem stages createLivePipeline sequences (used in fault labels). */
export type LiveStage = 'capture-start' | 'recording-start' | 'engine-start';

/**
 * Serialize a thrown value for a LOG LINE (#593 A1). Prefer the stack (names the throwing frame),
 * else `name: message`, else a safe JSON — NEVER `String(e)` (a DOM Event → "[object Event]", the
 * exact fidelity loss that hid the real #593 throw) and never bare `JSON.stringify` (throws on cycles).
 */
export function serr(e: unknown): string {
  const x = e as { message?: string; stack?: string; name?: string } | null | undefined;
  if (x?.stack) return x.stack;
  if (x?.message) return `${x.name ?? 'Error'}: ${x.message}`;
  try { return `non-error throw: ${JSON.stringify(e)}`; }
  catch { return `non-error throw: ${String(e)}`; }
}

export interface LivePipelineDeps {
  /** Attach the page-side capture; returns its teardown. Best-effort — a throw DEGRADES, never evicts. */
  startCapture: () => Promise<() => Promise<void>>;
  /** Attach the page-side recording (optional); returns its teardown. Best-effort. */
  startRecording?: () => Promise<() => Promise<void>>;
  /** The transcription engine (the BotPipeline). Its start() failure is non-fatal + retried. */
  engine: Pipeline;
  /** Loud fault sink — which stage failed + the raw error (wired to console.error(serr) + publishFault). */
  onFault: (stage: LiveStage, e: unknown) => void;
  /** Bounded retry for engine start (the pyannote model load). Default 3 attempts, 2s apart. */
  retry?: { attempts: number; delayMs: number };
}

/**
 * The LIVE pipeline (composition-root seam) — THE #593 FIX. Wraps the page-side capture + recording
 * attach and the transcription-engine start into ONE Pipeline whose `start()` ALWAYS RESOLVES.
 *
 * Once the bot is admitted, a post-admission subsystem failure — a page-side capture/MediaRecorder
 * throw, or the mixed-lane pyannote model load rejecting (empty HF cache / no egress) — must DEGRADE
 * LOUDLY, never propagate out of `start()`. The orchestrator's backstop maps ANY `pipeline.start()`
 * throw to `leave('pipeline_start_failed')` + `join_failure` (correct for a truly unrecoverable
 * pipeline, and deliberately preserved), so keeping every recoverable failure INSIDE this seam is
 * what stops the ~120 ms self-evict. Every failure routes to `onFault` (→ console + the transcript
 * fault publisher → meeting-page banner) so "admitted but not transcribing" is loud, not silent.
 *
 * Browser-free BY CONSTRUCTION (takes thunks; imports no playwright/DOM) so it is L2-unit-provable
 * offline — the admitted→capture-start seam no unit covered before (#593 A4). index.ts binds the
 * thunks to the live page.
 */
export function createLivePipeline(deps: LivePipelineDeps): Pipeline {
  const { startCapture, startRecording, engine, onFault } = deps;
  const maxAttempts = Math.max(1, deps.retry?.attempts ?? 3);
  const delayMs = Math.max(0, deps.retry?.delayMs ?? 2000);

  let stopCapture: (() => Promise<void>) | null = null;
  let stopRecording: (() => Promise<void>) | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  // Engine start with bounded background retry: the FIRST attempt is awaited by start() (so start()
  // resolves promptly — the bot is already seated); later attempts fire on a timer without ever
  // rejecting start(). A transient/slow model load thus self-heals without evicting the bot.
  const tryEngineStart = async (attempt: number): Promise<void> => {
    try {
      await engine.start();
    } catch (e) {
      onFault('engine-start', e);
      if (stopped || attempt >= maxAttempts) return;   // give up (already published loud); bot STAYS
      retryTimer = setTimeout(() => { retryTimer = null; void tryEngineStart(attempt + 1); }, delayMs);
    }
  };

  return {
    async start(): Promise<void> {
      // capture-start — best-effort (a page media Event / exposeFunction reject must not evict).
      try { stopCapture = await startCapture(); }
      catch (e) { onFault('capture-start', e); }
      // recording-start — best-effort.
      if (startRecording) {
        try { stopRecording = await startRecording(); }
        catch (e) { onFault('recording-start', e); }
      }
      // engine-start — non-fatal degrade + bounded retry (the pyannote model load; #593 root cause).
      await tryEngineStart(1);
      // NOTHING rethrows ⇒ the orchestrator never sees a pipeline.start() throw ⇒ no self-evict.
    },
    async stop(): Promise<void> {
      stopped = true;
      if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
      const sc = stopCapture; stopCapture = null;
      if (sc) await sc().catch(() => { /* best-effort — page may be closing */ });
      const sr = stopRecording; stopRecording = null;
      if (sr) await sr().catch(() => { /* best-effort — flush the final chunk → master assembly */ });
      await engine.stop().catch(() => { /* best-effort; idempotent across double-stop */ });
    },
  };
}
