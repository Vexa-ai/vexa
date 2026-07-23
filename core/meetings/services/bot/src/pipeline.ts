/**
 * Pipeline adapter (2b) — the capture → lane → STT → transcript.v1 engine, behind the
 * orchestrator's Pipeline port.
 *
 * The bot rewires NOTHING about transcription topology: the per-channel lane (turn gating,
 * LocalAgreement confirmation, glow→channel naming) lives in @vexa/gmeet-pipeline, the mixed
 * lane (pyannote cut + hints naming, no diarizer) in @vexa/mixed-pipeline, and the stt.v1
 * round-trip in @vexa/transcribe-whisper. This adapter only:
 *   1. picks the lane on inv.platform (google_meet → gmeet/per-channel; zoom/teams/jitsi → mixed),
 *   2. injects the stt port (TranscriptionClient.transcribe) + a TranscriptSink, and
 *   3. RECONCILES the lane's TranscriptSink (segment/draft/finalize — owned by the lane's
 *      transcript.v1 contract) onto the bot's injected TranscriptSink.publish(segment) port.
 *
 * The capture BRIDGE (browser page-side capture → feedAudio) lives in capture-bridge.ts and is
 * L4-gated; this file is the offline-provable lane composition (L2/L3 via pipeline.test.ts:
 * synthetic PCM → mock transcribe → assert publish got transcript.v1-valid segments).
 *
 * Ported from services/vexa-bot_new/src/pipeline/gmeet-pipeline.ts (the lane wrapper) onto the
 * v0.12 ports; the mixed branch wires @vexa/mixed-pipeline's ChunkedTranscriber for Zoom/Teams.
 */
import {
  createGmeetPipeline,
  type TranscriptSink as LaneTranscriptSink,
  type TranscriptSegment as LaneSegment,
  type SpeakerStreamManagerConfig,
} from '@vexa/gmeet-pipeline';
import {
  ChunkedTranscriber,
  PyannoteSegmenter,
  type BoundaryEvent,
  type ChunkSegment,
  type ChunkedTranscriberCallbacks,
  type HintKind,
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
export type MixedTranscriber = Pick<ChunkedTranscriber, 'feedAudio' | 'recordHint' | 'dispose'>;
export type MixedTranscriberFactory = (cb: ChunkedTranscriberCallbacks) => Promise<MixedTranscriber>;

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
    speaker: seg.speaker,
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

interface PublishTracker {
  publish(segment: TranscriptSegment): void;
  drain(): Promise<void>;
}

/** Track the async transcript egress behind each lane's synchronous callback surface. Teardown
 * drains this set, so a final lane flush reaches Redis before lifecycle truth says completed. */
function createPublishTracker(
  publish: TranscriptSink['publish'],
  onError?: (e: unknown) => void,
): PublishTracker {
  const inflight = new Set<Promise<void>>();
  const reportError = (error: unknown): void => {
    try {
      (onError ?? ((err) => console.error(`[bot] pipeline: transcript publish rejected: ${String(err)}`)))(error);
    } catch { /* error reporting cannot break teardown */ }
  };
  return {
    publish(segment) {
      const pending = Promise.resolve()
        .then(() => publish(segment))
        .catch(reportError);
      inflight.add(pending);
      void pending.then(
        () => inflight.delete(pending),
        () => inflight.delete(pending),
      );
    },
    async drain() {
      while (inflight.size) await Promise.all([...inflight]);
    },
  };
}

/**
 * The sink ADAPTER — the load-bearing reconciliation. The lane emits via `segment` (confirmed),
 * `draft` (live partial, completed:false), `finalize` (session end); the bot's port is a single
 * `publish(segment)`. We forward BOTH `segment` and `draft` to publish (the bot's transcript.v1
 * egress carries `completed` to distinguish confirmed from draft) and treat `finalize` as a
 * no-op at this seam (the bot signals end-of-session via lifecycle.v1, not the transcript stream).
 */
function laneSink(egress: PublishTracker): LaneTranscriptSink {
  const forward = (seg: LaneSegment): void => {
    egress.publish(toBotSegment(seg));
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
function chunkToBotSegment(speaker: string, c: ChunkSegment, completed: boolean): TranscriptSegment {
  return {
    segment_id: c.segmentId,
    // Provisional cluster ids (seg_N) are an internal key, never a display name; while
    // unattributed, emit the stable 'Speaker' label the gmeet lane uses (gmeet-pipeline.ts:52)
    // so per-speaker consumers group as one speaker, not hundreds; late attribution still
    // repaints by segment_id.
    speaker: /^seg_\d+$/.test(speaker) ? 'Speaker' : speaker,
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

/** Build the gmeet (per-channel) BotPipeline. The lane is lazy — it begins on the first fed
 *  frame (post-admission), so start() is a no-op and stop() disposes (flush every turn → finalize). */
function createGmeetBotPipeline(
  transcribe: Transcribe,
  sink: TranscriptSink,
  config?: SpeakerStreamManagerConfig,
  onError?: (e: unknown) => void,
): BotPipeline {
  const egress = createPublishTracker(sink.publish.bind(sink), onError);
  const lane = createGmeetPipeline({ transcribe, sink: laneSink(egress), config, onError });
  return {
    async start() { /* lane is lazy — begins on the first fed frame */ },
    async stop() { await lane.dispose(); await egress.drain(); },
    feedAudio: (channel, glowName, pcm, tsMs) => lane.feedAudio(channel, glowName, pcm, tsMs),
    feedMixedAudio() { /* not the gmeet lane */ },
    recordHint() { /* not the gmeet lane */ },
  };
}

/** Build the mixed (Zoom/Teams) BotPipeline over @vexa/mixed-pipeline's ChunkedTranscriber.
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
  /** O-TEL-1: tee each segmenter CUT into the recorded session. The mixed lane's chunking is
   *  decided entirely by these, so a session without them cannot be replayed faithfully — an
   *  offline harness has to substitute its own cut source and then measures the substitute. */
  onBoundary?: (ev: { kind: string; tMs: number; confidence?: number }) => void,
): BotPipeline {
  let transcriber: MixedTranscriber | null = null;
  let creating: Promise<MixedTranscriber> | null = null;
  const hintCounters: HintCounters = { received: 0, matched: 0, missed: 0 };
  const egress = createPublishTracker(sink.publish.bind(sink), onError);

  const publish = (speaker: string, segs: ChunkSegment[], completed: boolean): void => {
    for (const c of segs) {
      egress.publish(chunkToBotSegment(speaker, c, completed));
    }
  };

  const ensure = (): Promise<MixedTranscriber> => {
    if (transcriber) return Promise.resolve(transcriber);
    if (!creating) {
      creating = createTranscriber({
        transcribe,
        // ONE atomic bundle: newly-confirmed (persisted) + the surviving pending tail.
        publish: (speaker, confirmed, pending) => { publish(speaker, confirmed, true); publish(speaker, pending, false); },
        publishPending: (speaker, segments) => publish(speaker, segments, false),
        clearPending: () => { /* the bot's transcript.v1 egress is append-only; drafts self-replace by id */ },
        rename: (_oldSpeaker, newSpeaker, segments) => publish(newSpeaker, segments, true),
        language,
        onError,
        // C1 hop 4: the binder's instantaneous verdict per hint — a hint with no
        // overlapping turn increments `missed` (loudly, on the periodic counter line).
        onHintOutcome: (o) => { if (o.outcome === 'matched') hintCounters.matched++; else hintCounters.missed++; },
        // Wrap the REAL segmenter (the default when this is absent) so production keeps its own
        // cut source and the recorder merely observes it.
        ...(onBoundary ? {
          makeSegmenter: (sink: (ev: BoundaryEvent) => void) =>
            PyannoteSegmenter.create({
              inferIntervalMs: 500,
              onBoundary: (ev: BoundaryEvent) => {
                try { onBoundary({ kind: String(ev.kind), tMs: ev.tMs, confidence: ev.confidence }); }
                catch { /* telemetry must never break the cut path */ }
                sink(ev);
              },
            }),
        } : {}),
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
    async stop() { if (transcriber) await transcriber.dispose(); await egress.drain(); },
    feedAudio() { /* not the mixed lane (mixed has no per-channel glow) */ },
    feedMixedAudio: (pcm, tsMs) => { transcriber?.feedAudio(pcm, tsMs); },
    // C1 hop 3 + C2: count the arrival, forward under the platform's TRUE kind.
    recordHint: (name, tMs, isEnd) => { hintCounters.received++; transcriber?.recordHint(name, hintKind, tMs, isEnd); },
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
 * The composition-root factory: pick the lane on platform and wire stt + sink. Google Meet
 * uses the per-channel (overlap-safe, glow-named) lane; Zoom/Teams/Jitsi use the mixed lane.
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
    /** O-TEL-1: observe each segmenter cut (the recorder stores them so a mixed session replays
     *  with production's own chunking instead of a substitute). */
    onBoundary?: (ev: { kind: string; tMs: number; confidence?: number }) => void;
  } = {},
): BotPipeline {
  const transcribe = opts.transcribe ?? createTranscribe(inv);
  if (isMixedLanePlatform(inv.platform)) {
    return createMixedBotPipeline(
      transcribe, sink, hintKindForPlatform(inv.platform),
      inv.language ?? undefined, opts.onError, opts.createMixedTranscriber, opts.onBoundary,
    );
  }
  return createGmeetBotPipeline(transcribe, sink, opts.config, opts.onError);
}

/** The post-admission subsystem stages createLivePipeline sequences (used in fault labels). */
export type LiveStartStage = 'capture-start' | 'recording-start' | 'engine-start';
export type LiveTeardownStage = 'capture-stop' | 'recording-finalize' | 'engine-stop';
export type LiveStage = LiveStartStage | LiveTeardownStage;
export type LiveStagePhase = 'started' | 'finished';

export const DEFAULT_LIVE_TEARDOWN_TIMEOUT_MS: Readonly<Record<LiveTeardownStage, number>> = {
  'capture-stop': 2_000,
  'recording-finalize': 6_000,
  'engine-stop': 6_000,
};

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
  /** Teardown breadcrumbs. A started stage emits either finished or onFault, so a stuck stage is
   *  named without a debugger. Telemetry is best-effort and may not break the data path. */
  onStage?: (stage: LiveTeardownStage, phase: LiveStagePhase, elapsedMs: number) => void;
  /** Per-stage teardown deadlines. Capture closes ingress first; recording and engine then share
   *  the remaining wall-clock window in parallel. */
  teardownTimeoutMs?: Partial<Record<LiveTeardownStage, number>>;
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
  const { startCapture, startRecording, engine, onFault, onStage } = deps;
  const maxAttempts = Math.max(1, deps.retry?.attempts ?? 3);
  const delayMs = Math.max(0, deps.retry?.delayMs ?? 2000);

  let stopCapture: (() => Promise<void>) | null = null;
  let stopRecording: (() => Promise<void>) | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let engineStartPromise: Promise<void> | null = null;
  let stopped = false;
  let stopPromise: Promise<void> | null = null;

  const reportFault = (stage: LiveStage, e: unknown): void => {
    try { onFault(stage, e); } catch { /* telemetry must never break lifecycle */ }
  };
  const reportStage = (stage: LiveTeardownStage, phase: LiveStagePhase, elapsedMs: number): void => {
    try { onStage?.(stage, phase, elapsedMs); } catch { /* telemetry must never break lifecycle */ }
  };
  const stageTimeoutMs = (stage: LiveTeardownStage): number => {
    const configured = deps.teardownTimeoutMs?.[stage];
    return Number.isFinite(configured) && (configured ?? 0) > 0
      ? configured as number
      : DEFAULT_LIVE_TEARDOWN_TIMEOUT_MS[stage];
  };
  const runTeardownStage = async (
    stage: LiveTeardownStage,
    operation: (() => Promise<void>) | null,
  ): Promise<void> => {
    if (!operation) return;
    const startedAt = Date.now();
    const timeoutMs = stageTimeoutMs(stage);
    reportStage(stage, 'started', 0);

    let timer: ReturnType<typeof setTimeout> | null = null;
    const settled = Promise.resolve()
      .then(operation)
      .then(
        () => ({ kind: 'finished' as const }),
        (error: unknown) => ({ kind: 'fault' as const, error }),
      );
    const deadline = new Promise<{ kind: 'timeout' }>((resolve) => {
      timer = setTimeout(() => resolve({ kind: 'timeout' }), timeoutMs);
    });
    const outcome = await Promise.race([settled, deadline]);
    if (timer) clearTimeout(timer);
    const elapsedMs = Date.now() - startedAt;

    if (outcome.kind === 'finished') {
      reportStage(stage, 'finished', elapsedMs);
    } else if (outcome.kind === 'fault') {
      reportFault(stage, outcome.error);
    } else {
      reportFault(stage, new Error(`${stage} timed out after ${timeoutMs}ms`));
    }
  };

  // Engine start with bounded background retry: the FIRST attempt is awaited by start() (so start()
  // resolves promptly — the bot is already seated); later attempts fire on a timer without ever
  // rejecting start(). A transient/slow model load thus self-heals without evicting the bot.
  const tryEngineStart = async (attempt: number): Promise<void> => {
    try {
      await engine.start();
    } catch (e) {
      reportFault('engine-start', e);
      if (stopped || attempt >= maxAttempts) return;   // give up (already published loud); bot STAYS
      retryTimer = setTimeout(() => { retryTimer = null; void launchEngineStart(attempt + 1); }, delayMs);
    }
  };
  const launchEngineStart = (attempt: number): Promise<void> => {
    const pending = tryEngineStart(attempt);
    engineStartPromise = pending;
    void pending.then(
      () => { if (engineStartPromise === pending) engineStartPromise = null; },
      () => { if (engineStartPromise === pending) engineStartPromise = null; },
    );
    return pending;
  };

  return {
    async start(): Promise<void> {
      // capture-start — best-effort (a page media Event / exposeFunction reject must not evict).
      try { stopCapture = await startCapture(); }
      catch (e) { reportFault('capture-start', e); }
      // recording-start — best-effort.
      if (startRecording) {
        try { stopRecording = await startRecording(); }
        catch (e) { reportFault('recording-start', e); }
      }
      // engine-start — non-fatal degrade + bounded retry (the pyannote model load; #593 root cause).
      await launchEngineStart(1);
      // NOTHING rethrows ⇒ the orchestrator never sees a pipeline.start() throw ⇒ no self-evict.
    },
    stop(): Promise<void> {
      // The orchestrator owns normal stop, and index.ts calls stop again in finally. If an outer
      // deadline fires while a teardown operation is still pending, a second independent stop
      // would otherwise overlap stages or hang again. One cached promise makes teardown single-flight.
      if (stopPromise) return stopPromise;
      stopped = true;
      if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
      const sc = stopCapture; stopCapture = null;
      const sr = stopRecording; stopRecording = null;
      stopPromise = (async () => {
        // Stop ingress first. Once capture's Node-side gate closes, recording finalization and
        // transcript-engine disposal are independent and may consume their budgets in parallel.
        await runTeardownStage('capture-stop', sc);
        await Promise.all([
          runTeardownStage('recording-finalize', sr),
          runTeardownStage('engine-stop', async () => {
            // A background retry may already be creating the transcriber. Let that attempt settle,
            // then stop the resulting engine; otherwise a late success can resurrect it after teardown.
            const starting = engineStartPromise;
            if (starting) await starting;
            await engine.stop();
          }),
        ]);
      })();
      return stopPromise;
    },
  };
}
