/**
 * @vexa/bot — the COMPOSITION ROOT (P7 worker entrypoint).
 *
 * The ONLY place wiring happens (Seemann): validate the invocation.v1 config from the env,
 * build the real adapters for every port, hand them to the pure orchestrator, run to a
 * terminal lifecycle.v1 state, and exit. The container boots here, works, emits, and dies.
 *
 * ┌─ INCREMENT 2a wired the LIVE redis/HTTP transports for the data/control plane:
 * │    • LifecycleSink  → HTTP POST to inv.meetingApiCallbackUrl (lifecycle.v1, retry/backoff)  ✅ LIVE
 * │    • TranscriptSink → redis stream + pub/sub (transcript.v1)                                ✅ LIVE
 * │    • ActsSource     → redis pub/sub on actsChannel(meetingId) (acts.v1)                     ✅ LIVE
 * ├─ INCREMENT 2b wires the browser join + capture + recording (THIS file):
 * │    • JoinDriver     → @vexa/join.joinMeeting over a @vexa/remote-browser page               ✅ WIRED (L4)
 * │    • Pipeline       → capture bridge → @vexa/{gmeet,mixed}-pipeline → @vexa/transcribe-whisper ✅ WIRED (L4 capture · L2/L3 lane)
 * │    • RecordingSink  → per-chunk upload to inv.recordingUploadUrl (master assembled server-side) ✅ WIRED (L4 upload · L3 sink)
 * │    • Speak          → acts.v1 `speak`/`speak_stop` → meeting-UI mic + VM TTS chain          ✅ WIRED (L4)
 * └─ The browser/capture/recording-upload/speak legs are BROWSER- or VM-resident → L4-gated
 *    (proven by the O6 VM run, not unit tests). The lane + assembler cores are L2/L3-proven.
 *
 * Robustness: if the browser cannot be launched (e.g. no display in a non-VM context), the
 * composition root falls back to NO browser session and the orchestrator drives to a clean
 * terminal `failed` (join_failure) rather than crashing the root — same disposability as the
 * lazy redis connect.
 */
import { createClient } from 'redis';
import { loadInvocation, InvocationError, speakerStreamConfigFromEnv, type Invocation } from './config.js';
import type { Act, LifecycleEvent } from './contracts.js';
import { createOrchestrator } from './orchestrator.js';
import { createHttpLifecycleSink } from './adapters/lifecycle-http.js';
import { createRedisTranscriptSink, redisClientFrom } from './adapters/transcript-redis.js';
import { createRedisActsSource, redisActsClientFrom } from './adapters/acts-redis.js';
import { createBrowserJoinDriver } from './join-driver.js';
import { createBotPipeline, createLivePipeline, createTranscribe, serr, type BotPipeline } from './pipeline.js';
import { createBotRecordingSink } from './recording.js';
import { createCaptureSignalRecorder, wrapTranscribeWithTap, type CaptureSignalRecorder } from './telemetry.js';
import { createSttFaultReporter } from './stt-faults.js';
import { launchBrowser, startCaptureBridge, startRecording, createSpeakController, type BrowserSession, type SpeakController } from './capture-bridge.js';
import { installSignalHandlers } from './signals.js';
import type {
  JoinDriver,
  Pipeline,
  LifecycleSink,
  TranscriptSink,
  ActsSource,
  RecordingSink,
} from './ports.js';

/** A console-only lifecycle sink — used for self-host (no `meetingApiCallbackUrl`) and as the
 *  pre-config fallback. The live HTTP sink (createHttpLifecycleSink) replaces it when a
 *  callback URL is configured. */
function consoleLifecycleSink(): LifecycleSink {
  return { async emit(e: LifecycleEvent) { console.log(`[bot] lifecycle.v1 ${e.status}${e.completion_reason ? ` (${e.completion_reason})` : ''}${e.failure_stage ? ` @${e.failure_stage}` : ''}`); } };
}

/** A no-op JoinDriver used ONLY when the browser fails to launch — it reports a join failure so
 *  the orchestrator drives to a clean terminal `failed`(join_failure) instead of the root crashing. */
function noBrowserJoinDriver(reason: string): JoinDriver {
  return {
    async join() { console.error(`[bot] no browser session: ${reason}`); return 'error'; },
    onRemoval() { return () => { /* */ }; },
    async leave() { /* */ },
    async withdraw() { /* no browser — nothing to withdraw */ },
  };
}

/** An offline Pipeline used when there is no browser to capture from (browser-launch failure):
 *  it satisfies the port so the orchestrator can teardown cleanly; it never captures. */
function noBrowserPipeline(): Pipeline {
  return { async start() { /* */ }, async stop() { /* */ } };
}

/** The meeting id that keys the redis transcript/acts channels (0.11 control-plane convention:
 *  the numeric `meeting_id`). Falls back to the platform native id / connection id when the
 *  numeric id is absent (e.g. self-host paths), so the channels are always well-formed. */
function meetingChannelId(inv: Invocation): string | number {
  return inv.meeting_id ?? inv.nativeMeetingId ?? inv.connectionId ?? 'session';
}

/**
 * Derive the hard active-phase cap (ms) from invocation.v1 `automaticLeave`.
 *
 *  `orchestrator.run({ maxActiveMs })` is a HARD ceiling on the active phase that resolves to
 *  `completed(max_bot_time_exceeded)` — a backstop so a bot can never live forever (the granular
 *  empty-room / waiting-room timeouts that map to left_alone/startup_alone are driven by the
 *  Pipeline/JoinDriver in 2b). This is a GENEROUS backstop (default 4h, override with
 *  BOT_MAX_ACTIVE_MS in ms) — the granular empty-room / waiting-room timeouts drive every NORMAL exit
 *  well before this fires. We floor it at max(everyoneLeft, noOneJoined, waitingRoom) + 60s margin so
 *  the backstop can never undercut those granular timeouts. */
const DEFAULT_MAX_ACTIVE_MS = 4 * 60 * 60 * 1000; // 4 hours
function deriveMaxActiveMs(inv: Invocation, env: NodeJS.ProcessEnv = process.env): number {
  const al = inv.automaticLeave ?? {};
  const everyoneLeft = al.everyoneLeftTimeout ?? 120_000;
  const noOneJoined = al.noOneJoinedTimeout ?? 600_000;
  const waitingRoom = al.waitingRoomTimeout ?? 300_000;
  const MARGIN_MS = 60_000; // give the granular timeouts room to fire first
  const floor = Math.max(everyoneLeft, noOneJoined, waitingRoom) + MARGIN_MS;
  const override = Number(env.BOT_MAX_ACTIVE_MS);
  const cap = Number.isFinite(override) && override > 0 ? override : DEFAULT_MAX_ACTIVE_MS;
  return Math.max(cap, floor);
}

/**
 * Tee an ActsSource so EVERY act reaches both the orchestrator (its single `handle`, which owns
 * `leave`) AND the bot's voice handler (speak / speak_stop), from ONE underlying subscription.
 * The orchestrator stays the pure core (it never imports the SpeakController); the voice path is
 * wired here at the composition root. The orchestrator's `subscribe(handler)` registers its
 * handler; we fan the live source's messages to it plus `voice`.
 */
function teeActs(source: ActsSource, voice: (act: Act) => void | Promise<void>): ActsSource {
  return {
    subscribe(handler) {
      return source.subscribe((act) => {
        void Promise.resolve(handler(act)).catch((e) => console.error(`[bot] acts: orchestrator handler rejected: ${String(e)}`));
        void Promise.resolve(voice(act)).catch((e) => console.error(`[bot] acts: voice handler rejected: ${String(e)}`));
      });
    },
  };
}

/** The bot's voice-act handler: route acts.v1 speak / speak_stop to the SpeakController. The
 *  other voice acts (chat/screen/avatar) are out of this increment's scope. */
function voiceHandler(speak: SpeakController): (act: Act) => Promise<void> {
  return async (act) => {
    if (act.action === 'speak') await speak.speak(act.text, act.voice);
    else if (act.action === 'speak_stop') await speak.stop();
  };
}

/**
 * Probe the SECONDARY control-plane channel (redis) for the reachability gate (#530). A fresh,
 * short-lived connection with a bounded connect timeout and NO reconnection — a single yes/no on
 * whether redis answers a PING. Never throws: any fault resolves to `false` (unreachable). Uses a
 * throwaway client (not the lazy transcript/acts clients) so the probe can't disturb their state.
 */
async function pingRedis(redisUrl: string, timeoutMs = 3000): Promise<boolean> {
  const client = createClient({ url: redisUrl, socket: { connectTimeout: timeoutMs, reconnectStrategy: false } });
  client.on('error', () => { /* swallow — the probe's verdict is the return value, not a throw */ });
  try {
    await client.connect();
    const pong = await client.ping();
    return pong === 'PONG';
  } catch {
    return false;
  } finally {
    await client.disconnect().catch(() => { /* best-effort */ });
  }
}

export async function main(env: NodeJS.ProcessEnv = process.env): Promise<number> {
  // ── validate config (P14: fail fast) ──
  let inv: Invocation;
  try {
    inv = loadInvocation(env);
  } catch (e) {
    if (e instanceof InvocationError) {
      // No valid connection_id to attribute the failure to → emit a best-effort terminal
      // event and exit non-zero. We have no validated callbackUrl yet, so this goes to the
      // console sink. (The live HTTP sink would POST validation_error once a URL is known.)
      console.error(`[bot] FATAL ${e.message}`);
      consoleLifecycleSink().emit({ connection_id: env.VEXA_CONNECTION_ID ?? '', status: 'failed', failure_stage: 'requested', completion_reason: 'validation_error', reason: e.message, exit_code: 1 }).catch(() => {});
      return 1;
    }
    throw e;
  }

  // ── build the LIVE transports → the pure orchestrator ──
  const meetingId = meetingChannelId(inv);

  // lifecycle.v1: HTTP POST to meeting-api when a callback URL is configured; console-only for
  // self-host (no callback). The HTTP sink retries/backs off and never throws out of emit.
  const lifecycle: LifecycleSink = inv.meetingApiCallbackUrl
    ? createHttpLifecycleSink({ callbackUrl: inv.meetingApiCallbackUrl, internalSecret: inv.internalSecret })
    : consoleLifecycleSink();

  // transcript.v1 + acts.v1: redis. Connect LAZILY — constructing the clients does NOT dial
  // redis, so an unreachable broker doesn't crash the composition root; the first publish/
  // subscribe surfaces the error and the orchestrator drives to a clean terminal `failed`.
  const transcriptClient = redisClientFrom(inv.redisUrl);
  const actsClient = redisActsClientFrom(inv.redisUrl);
  const transcript: TranscriptSink = createRedisTranscriptSink({
    client: transcriptClient, meetingId, nativeMeetingId: inv.nativeMeetingId,
  });
  const liveActs = createRedisActsSource({ client: actsClient, meetingId });

  // ── 2b: launch the browser + wire join / capture / recording / speak (L4-gated). ──
  // Browser-launch failure must NOT crash the root: fall back to the no-browser drivers so the
  // orchestrator still emits a clean terminal failed(join_failure).
  let session: BrowserSession | null = null;
  let join: JoinDriver;
  let pipeline: Pipeline;
  let botPipeline: BotPipeline | null = null;
  let acts: ActsSource = liveActs;
  const recording = inv.recordingEnabled ? createBotRecordingSink({ inv, log: (m) => console.log(`[bot] ${m}`) }) : undefined;
  // O-TEL-1: persist the raw captured-signal.v1 stream for offline replay. Off ⇒ the tap is a
  // single undefined-check and the capture path is byte-for-byte unchanged. VEXA_CAPTURE_SIGNAL=1
  // enables it without a control plane (the local hot-loop path).
  const signalRecorder: CaptureSignalRecorder | null =
    (inv.captureSignalEnabled ?? env.VEXA_CAPTURE_SIGNAL === '1')
      ? createCaptureSignalRecorder(inv)
      : null;
  if (signalRecorder) console.log(`[bot] capture-signal recording → ${signalRecorder.path}`);
  // Counts STT failures across the meeting so the terminal lifecycle event can carry WHY a
  // transcript is short or empty, instead of leaving it indistinguishable from a silent room.
  const sttFaults = createSttFaultReporter();
  const speakerStreamConfig = speakerStreamConfigFromEnv(env);
  if (speakerStreamConfig) console.log(`[bot] speaker-stream tuning enabled: ${JSON.stringify(speakerStreamConfig)}`);

  try {
    session = await launchBrowser(inv);                                   // L4 (O6/VM)
    join = createBrowserJoinDriver(session.page, inv);
    botPipeline = createBotPipeline(inv, transcript, {
      // When recording, tee every STT round-trip to <session>.stt.jsonl (the capture/STT/assembly bisect).
      transcribe: signalRecorder ? wrapTranscribeWithTap(createTranscribe(inv), signalRecorder.path) : undefined,
      config: speakerStreamConfig,
      // Record production's OWN cuts alongside the audio, so a replay of this session chunks the
      // way the meeting actually chunked.
      onBoundary: signalRecorder ? (ev) => signalRecorder.sink.captureBoundary?.({ type: 'boundary', ...ev }) : undefined,
      // Every STT fault is counted and carried out on the terminal lifecycle event (see
      // sttFaults). Logging it here as well keeps the raw line for anyone tailing the container.
      onError: (e) => { sttFaults.record(e); console.error(`[bot] pipeline fault: ${String(e)}`); },
    });
    // Defer the page-side capture start to pipeline.start(): the orchestrator calls it AFTER
    // admission (orchestrator.ts:125), on the LIVE meeting page — where addInitScript has injected
    // window.VexaBrowserUtils and the participant <audio> elements exist. Starting it at launch ran
    // the page.evaluate on the BLANK pre-navigation page (no VexaBrowserUtils, no audio), and the
    // subsequent goto to the meeting URL destroyed that context — so capture never attached. (L4.)
    const sess = session, bp = botPipeline, rec = recording;
    // In-meeting chat (jitsi lane) → a transcript.v1 `chat` segment: the sender is the
    // speaker, the wall clock is the timing (epoch seconds, like the audio lanes), and
    // `completed` is immediate — a chat line has no draft phase.
    let chatSeq = 0;
    const publishChat = (sender: string, text: string): void => {
      const nowMs = Date.now();
      void transcript.publish({
        segment_id: `${inv.connectionId ?? 'session'}:chat:${nowMs}:${chatSeq++}`,
        speaker: sender,
        speaker_key: `chat:${sender}`,
        text,
        start: nowMs / 1000,
        end: nowMs / 1000,
        completed: true,
        source: 'chat',
        absolute_start_time: new Date(nowMs).toISOString(),
        absolute_end_time: new Date(nowMs).toISOString(),
      }).catch((e) => console.error(`[bot] chat publish rejected: ${String(e)}`));
    };
    // #593: a post-admission subsystem failure must NEVER self-evict. createLivePipeline wraps the
    // page-side capture + recording attach + the engine start so pipeline.start() ALWAYS RESOLVES;
    // each failure surfaces LOUD via onFault (console with a full-fidelity serr(e)) instead of
    // throwing into the orchestrator's leave-on-fail backstop (which would hang the bot up).
    pipeline = createLivePipeline({
      startCapture: () => startCaptureBridge(sess.page, inv, bp, signalRecorder?.sink, publishChat),   // on the live meeting page
      startRecording: rec ? () => startRecording(sess.page, inv, rec) : undefined,          // MediaRecorder → recording.v1
      engine: bp,
      onFault: (stage, e) => {
        console.error(`[bot] live-pipeline: ${stage} failed (non-fatal, bot stays seated): ${serr(e)}`);
      },
    });
    // Voice: tee acts so `speak`/`speak_stop` reach the SpeakController (gated on voiceAgentEnabled).
    const speak = createSpeakController(session.page, inv);
    acts = teeActs(liveActs, voiceHandler(speak));
  } catch (e) {
    console.error(`[bot] browser launch/capture wiring failed — falling back to clean terminal failed: ${String(e)}`);
    join = noBrowserJoinDriver(String(e));
    pipeline = noBrowserPipeline();
    acts = liveActs;
  }

  // Reachability gate (#530): only meaningful under a control plane (a callback URL is set). When
  // present, the orchestrator makes the first `joining` emit load-bearing and, if the callback is
  // unreachable, probes redis before refusing to join. Self-host (no callback) has no gate.
  const reachability = inv.meetingApiCallbackUrl
    ? { probeSecondary: () => pingRedis(inv.redisUrl) }
    : undefined;

  const orchestrator = createOrchestrator(inv, {
    lifecycle,
    join,
    pipeline,
    acts,
    recording: recording as RecordingSink | undefined,
    reachability,
    degraded: () => sttFaults.report(),
  });

  // Disposability (P7): a termination signal ends the active phase gracefully (leave → flush →
  // terminal callback → exit 0) so the container never hangs after `active` — BOUNDED by the
  // force-exit watchdog in signals.ts (<25s, inside the runtime's SIGTERM→SIGKILL stop grace) so
  // a wedged teardown can never ride a `docker stop` all the way to a silent 137 (the incident's
  // exit code on BOTH orphaned bots). Wire before run(); release the listeners after.
  const releaseSignals = installSignalHandlers({ stop: (reason) => orchestrator.stop(reason) });
  try {
    const result = await orchestrator.run({ maxActiveMs: deriveMaxActiveMs(inv) });
    return result.exitCode;
  } finally {
    releaseSignals();
    // Tear down the pipeline (capture bridge + recording + engine) + browser (best-effort — a
    // teardown failure must not change the exit code). The orchestrator already stopped the pipeline
    // on a normal end; createLivePipeline.stop() is idempotent, and this also covers an early-exit
    // path that skipped the orchestrator's teardown. (#593)
    await pipeline.stop().catch(() => { /* best-effort */ });
    await signalRecorder?.close().catch(() => { /* best-effort */ });
    if (session) await session.close().catch(() => { /* best-effort */ });
    // Quit the redis connections on teardown (best-effort — a quit failure must not change the
    // exit code; they may never have connected if redis was unreachable).
    await transcriptClient.quit().catch(() => { /* best-effort */ });
    await actsClient.quit().catch(() => { /* best-effort */ });
  }
}

// Worker entrypoint: boot, work, emit, die (P7).
if (import.meta.url === `file://${process.argv[1]}`) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e); process.exit(1); });
}
