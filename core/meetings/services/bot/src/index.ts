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
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createClient } from 'redis';
import { isMixedLanePlatform, loadInvocation, InvocationError, speakerStreamConfigFromEnv, withLatencyProfile, type Invocation } from './config.js';
import type { Act, LifecycleEvent, TranscriptSegment } from './contracts.js';
import { createOrchestrator } from './orchestrator.js';
import { createHttpLifecycleSink } from './adapters/lifecycle-http.js';
import { createRedisTranscriptSink, redisClientFrom } from './adapters/transcript-redis.js';
import { createRedisActsSource, redisActsClientFrom } from './adapters/acts-redis.js';
import { createBrowserJoinDriver } from './join-driver.js';
import { createBotPipeline, createLivePipeline, createTranscribe, languageControlFrom, serr, type BotPipeline } from './pipeline.js';
import { languageActHandler, observationOf, summarize, type LanguageObservation } from './transcription-language.js';
import { createBotRecordingSink } from './recording.js';
import { createCaptureSignalRecorder, startBotLogSidecar, wrapTranscribeWithTap, wrapTranscriptWithSnapshot, type CaptureSignalRecorder } from './telemetry.js';
import { uploadSignalTapes } from './signal-upload.js';
import { describeGaps, mergeDegraded, transcriptGapsReport } from './transcript-gaps.js';
import { createSttFaultReporter } from './stt-faults.js';
import { withNavigationRefusal } from './meeting-navigation.js';
import { launchBrowser, startCaptureBridge, startRecording, restartMixedCapture, createSpeakController, createChatSender, type BrowserSession } from './capture-bridge.js';
import { createActDispatch } from './act-dispatch.js';
import { createDeafLaneMonitor, createRemoteAudioActivityTap, deafLaneFault, createSilenceAlonenessSource, resolveAloneSilenceWindowMs } from './aloneness.js';
import { installSignalHandlers } from './signals.js';
import { createVoiceNoteJoinDriver, createVoiceNotePipeline, createVoiceNoteSource, voiceNoteIdleMs, voiceNoteMaxActiveMs } from './voice-note.js';
import { createRedisAudioStreamReader, redisStreamClientFrom, type LiveRedisStreamClient } from './adapters/voice-note-redis.js';
import { createSessionEnd } from './adapters/redis-terminal.js';
import { callMaxActiveMs, createCallJoinDriver, createCallPipeline, createCallSource } from './call.js';
import { createLiveKitRoom, type LiveKitSdk } from './adapters/livekit-room.js';
import { loadScript, runPresenter, withPresenter, type Presenter, type PresenterRun, type PresenterSdk } from './presenter.js';
import { runBrowserSession } from './browser-session.js';
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
    async join() { console.error(`[bot] no browser session: ${reason}`); return { outcome: 'error', reason: `no browser session: ${reason}` }; },
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
 *  silence timeout that maps to left_alone is driven by the injected AlonenessSource). This is a
 *  GENEROUS backstop (default 4h, override with BOT_MAX_ACTIVE_MS in ms). We floor it at the largest
 *  configured lifecycle timeout + 60s so it cannot undercut a more specific lifecycle verdict. */
const DEFAULT_MAX_ACTIVE_MS = 4 * 60 * 60 * 1000; // 4 hours
function deriveMaxActiveMs(inv: Invocation, everyoneLeftMs: number, env: NodeJS.ProcessEnv = process.env): number {
  const al = inv.automaticLeave ?? {};
  const noOneJoined = al.noOneJoinedTimeout ?? 600_000;
  const waitingRoom = al.waitingRoomTimeout ?? 300_000;
  const MARGIN_MS = 60_000; // give the granular timeouts room to fire first
  const floor = Math.max(everyoneLeftMs, noOneJoined, waitingRoom) + MARGIN_MS;
  const override = Number(env.BOT_MAX_ACTIVE_MS);
  const cap = Number.isFinite(override) && override > 0 ? override : DEFAULT_MAX_ACTIVE_MS;
  return Math.max(cap, floor);
}

/**
 * Tee an ActsSource so EVERY act reaches the orchestrator (its single `handle`, which owns `leave`)
 * AND each side handler — in-meeting acts (speak / speak_stop / chat_send — act-dispatch.ts),
 * transcription language (reconfigure) — from ONE underlying subscription. The orchestrator stays
 * the pure core (it never imports the page controllers or the language control); the side paths
 * are wired here at the composition root.
 */
export function teeActs(
  source: ActsSource,
  sides: Record<string, (act: Act) => void | Promise<void>>,
): ActsSource {
  return {
    subscribe(handler) {
      return source.subscribe((act) => {
        void Promise.resolve(handler(act)).catch((e) => console.error(`[bot] acts: orchestrator handler rejected: ${String(e)}`));
        for (const [name, side] of Object.entries(sides)) {
          void Promise.resolve().then(() => side(act)).catch((e) => console.error(`[bot] acts: ${name} handler rejected: ${String(e)}`));
        }
      });
    },
  };
}

/**
 * Probe the SECONDARY control-plane channel (redis) for the reachability gate (#530). A fresh,
 * short-lived connection with a bounded connect timeout and NO reconnection — a single yes/no on
 * whether redis answers a PING. Never throws: any fault resolves to `false` (unreachable). Uses a
 * throwaway client (not the lazy transcript/acts clients) so the probe can't disturb their state.
 */
/** The demo presenter of a demo call (invocation.v1 `call.presenter`): its script is bundled in the
 *  image at `demo/<script>/`. A script that cannot be loaded starts nothing — the room stays
 *  empty and the call ends by its first-join window — and is logged. */
function startDemoPresenter(url: string, presenter: Presenter, sdk: unknown): PresenterRun | null {
  const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'demo', presenter.script);
  try {
    const { script, turns } = loadScript(presenter, (rel) => fs.readFileSync(path.join(dir, rel)));
    const run = runPresenter({ sdk: sdk as PresenterSdk, url, presenter, script, turns, log: (m) => console.log(m) });
    void run.done.then((r) => console.log(`[bot] demo presenter done: ${JSON.stringify(r)}`));
    return run;
  } catch (e) {
    console.error(`[bot] demo presenter not started: ${(e as Error).message}`);
    return null;
  }
}

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
  // ── browser-session mode: a bot-account sign-in or check, not a meeting (browser-session.ts) ──
  if (env.VEXA_BROWSER_SESSION) return runBrowserSession(env);

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
    ? createHttpLifecycleSink({ callbackUrl: inv.meetingApiCallbackUrl, token: inv.token })
    : consoleLifecycleSink();

  // transcript.v1 + acts.v1: redis. Connect LAZILY — constructing the clients does NOT dial
  // redis, so an unreachable broker doesn't crash the composition root; the first publish/
  // subscribe surfaces the error and the orchestrator drives to a clean terminal `failed`.
  // Every client stops reconnecting once the session is over: meeting-api removes this bot's Redis
  // user on the terminal callback, and a client that kept retrying would never let the bot exit.
  const redisSession = createSessionEnd();
  const transcriptClient = redisClientFrom(inv.redisUrl, { session: redisSession });
  const actsClient = redisActsClientFrom(inv.redisUrl, { session: redisSession });
  const liveTranscript = createRedisTranscriptSink({
    client: transcriptClient,
    meetingId,
    nativeMeetingId: inv.nativeMeetingId,
    token: inv.token,
    // Teams is the current blast radius. Its CSRC lanes need the same complete per-speaker pending
    // snapshot the Dashboard already consumes for GMeet-style live rendering. Leave every sibling
    // platform on the existing wire until this is proven on STAGE and deliberately imported back.
    liveEnvelope: inv.platform === 'teams' ? 'speaker-snapshot' : 'segment',
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
  // A voice note has no page to record from (its audio arrives as frames), so it records nothing here.
  const isVoiceNote = inv.platform === 'voice_note';
  // A call (#1783) is the same: its audio arrives as the room's tracks, not from a page.
  const isCall = inv.platform === 'vexa_call';
  const noBrowser = isVoiceNote || isCall;
  const recording = inv.recordingEnabled && !noBrowser ? createBotRecordingSink({ inv, log: (m) => console.log(`[bot] ${m}`) }) : undefined;
  // O-TEL-1: persist the raw captured-signal.v1 stream for offline replay. Off ⇒ the tap is a
  // single undefined-check and the capture path is byte-for-byte unchanged. VEXA_CAPTURE_SIGNAL=1
  // enables it without a control plane (the local hot-loop path).
  const signalRecorder: CaptureSignalRecorder | null =
    (inv.captureSignalEnabled ?? env.VEXA_CAPTURE_SIGNAL === '1')
      ? createCaptureSignalRecorder(inv)
      : null;
  if (signalRecorder) console.log(`[bot] capture-signal recording → ${signalRecorder.path}`);
  // The bot's own commentary, teed beside the tape. Started HERE — before the browser launches —
  // because the lines that explain a failed join are the ones emitted before anything else exists.
  const botLog = signalRecorder ? startBotLogSidecar(signalRecorder.botlogPath) : null;
  // The transcript a VIEWER is left with, folded live and written once at teardown. Every consumer
  // performs this fold (upsert by segment id, delete on retract) and nobody stored the result, so
  // "what did this meeting actually say?" could only be answered by re-running it against a redis
  // that no longer exists by then.
  const snapshot = signalRecorder ? wrapTranscriptWithSnapshot<TranscriptSegment, TranscriptSink>(liveTranscript, signalRecorder.transcriptPath) : null;
  const transcript: TranscriptSink = snapshot ?? liveTranscript;
  // Counts STT failures across the meeting so the terminal lifecycle event can carry WHY a
  // transcript is short or empty, instead of leaving it indistinguishable from a silent room.
  const sttFaults = createSttFaultReporter();
  const speakerStreamConfig = withLatencyProfile(inv.platform, speakerStreamConfigFromEnv(env), env);
  const remoteAudioActivity = createRemoteAudioActivityTap();
  const aloneSilenceWindowMs = resolveAloneSilenceWindowMs(inv.automaticLeave?.everyoneLeftTimeout, env);
  // #1192: the guard's one repair attempt, resolved late — the aloneness monitor is built before
  // the browser exists, and the restart needs the live page. Unset until the session launches
  // (and after a launch failure), in which case the guard just holds and keeps checking.
  let restartCapture: (() => void) | undefined;
  const aloneness = createSilenceAlonenessSource({
    activity: remoteAudioActivity,
    windowMs: aloneSilenceWindowMs,
    onCaptureFault: () => restartCapture?.(),
  });
  console.log(`[bot] aloneness: silence adapter + deaf-capture guard enabled (window_ms=${aloneSilenceWindowMs})`);
  // The deaf-lane guard: audio keeps arriving and no STT round-trip settles. Its incident is a
  // typed fault on the same accumulator the terminal lifecycle event reports, so a lane that went
  // deaf mid-meeting ends `degraded`, never as a clean meeting that just stopped talking.
  const transcribing = inv.transcribeEnabled !== false && !!inv.transcriptionServiceUrl;
  const deafLane = createDeafLaneMonitor({
    activity: remoteAudioActivity,
    onDeaf: (i) => sttFaults.record(deafLaneFault(i)),
  });
  const stopDeafLane = transcribing ? deafLane.start() : () => { /* nothing to guard */ };
  if (speakerStreamConfig) console.log(`[bot] speaker-stream tuning enabled: ${JSON.stringify(speakerStreamConfig)}`);

  // The live transcription-language setting: seeded from the invocation (validated at parse), then
  // replaced field by field by acts.v1 `reconfigure`. Every lane's next STT call reads it.
  const languageControl = languageControlFrom(inv);
  const observeLanguage = (obs: LanguageObservation): void => {
    try {
      signalRecorder?.sink.captureObservation?.({
        type: 'observation', t: Date.now(), source: 'transcription-language',
        lane: isMixedLanePlatform(inv.platform) ? 'mixed' : 'gmeet', observation: { ...obs },
      });
    } catch { /* an observation must never disturb the meeting */ }
  };
  console.log(`[bot] transcription language: ${summarize(languageControl.get())} (invocation)`);
  observeLanguage(observationOf(languageControl.get(), 'invocation'));
  const languageActs = languageActHandler(languageControl, { observe: observeLanguage });

// ── voice note (#1783): no browser, no join. The note's Redis stream is the capture; the lane,
  // STT and the sinks are the meeting bot's own. Stop (the end marker) ends the note `stopped`; a
  // browser that went away without Stop ends it `left_alone` after the idle timeout. ──
  let voiceStreamClient: LiveRedisStreamClient | null = null;
  let endVoiceNote: ((reason: 'stopped' | 'left_alone') => void) | null = null;
  let aloneHook: (() => void) | null = null;
  let callStats: (() => unknown) | null = null;
  if (isVoiceNote && inv.voiceNote) {
    const vn = inv.voiceNote;
    const engine = createBotPipeline(inv, transcript, {
      transcribe: signalRecorder ? wrapTranscribeWithTap(createTranscribe(inv, languageControl), signalRecorder.path) : undefined,
      languageControl,
      config: speakerStreamConfig,
      onError: (e) => { sttFaults.record(e); console.error(`[bot] pipeline fault: ${String(e)}`); },
    });
    botPipeline = engine;
    voiceStreamClient = redisStreamClientFrom(inv.redisUrl, { session: redisSession });
    const source = createVoiceNoteSource({
      reader: createRedisAudioStreamReader({ client: voiceStreamClient, key: vn.audioStream }),
      feed: (pcm, tsMs) => engine.feedAudio(0, vn.speakerName, pcm, tsMs),
      onEnd: () => endVoiceNote?.('stopped'),
      onIdle: () => endVoiceNote?.('left_alone'),
      idleMs: voiceNoteIdleMs(inv),
      log: (m) => console.log(m),
    });
    join = createVoiceNoteJoinDriver();
    const notePipeline = createVoiceNotePipeline({ source, engine });
    // The note's audio stream is read to its end before the lane flushes; its connection is closed
    // right then, before the terminal callback revokes this bot's Redis user. Left open, the revoke
    // dropped it and every reconnect logged "WRONGPASS invalid username-password pair".
    const streamClient = voiceStreamClient;
    pipeline = {
      start: () => notePipeline.start(),
      async stop() { await notePipeline.stop(); await streamClient.quit().catch(() => { /* best-effort */ }); },
    };
    console.log(`[bot] voice note: reading ${vn.audioStream} (cap ${vn.maxDurationSec}s)`);
  } else if (isCall && inv.call) {
    // ── call (#1783): no browser, no admission. The listener joins the LiveKit room hidden and
    // subscribe-only; each participant's microphone track is its own channel, named by the
    // participant, and so is the audio of a screen they share ("<name> (shared screen)"). The room emptying ends the call through the orchestrator's left_alone path. ──
    const c = inv.call;
    const engine = createBotPipeline(inv, transcript, {
      transcribe: signalRecorder ? wrapTranscribeWithTap(createTranscribe(inv), signalRecorder.path) : undefined,
      config: speakerStreamConfig,
      onError: (e) => { sttFaults.record(e); console.error(`[bot] pipeline fault: ${String(e)}`); },
    });
    botPipeline = engine;
    const sdk = (await import('@livekit/rtc-node')) as unknown as LiveKitSdk;
    const listenerRoom = createLiveKitRoom({ sdk, url: c.url, token: c.token, log: (m) => console.log(m),
      screenShareAudio: c.screenShareAudio !== false });
    const source = createCallSource({
      room: c.presenter ? withPresenter(listenerRoom, () => startDemoPresenter(c.url, c.presenter!, sdk)) : listenerRoom,
      feed: (channel, name, pcm, tsMs) => engine.feedAudio(channel, name, pcm, tsMs),
      onEmpty: (why) => { console.log(`[bot] call: ${why}`); aloneHook?.(); },
      emptyGraceMs: (c.emptyGraceSec ?? 30) * 1000,
      firstJoinTimeoutMs: (c.firstJoinTimeoutSec ?? 300) * 1000,
      log: (m) => console.log(m),
    });
    callStats = () => source.stats();
    join = createCallJoinDriver();
    pipeline = createCallPipeline({ source, engine });
    console.log(`[bot] call: listening in room ${c.room} (cap ${c.maxDurationSec}s)`);
  } else try {
    session = await launchBrowser(inv);                                   // L4 (O6/VM)
    join = withNavigationRefusal(createBrowserJoinDriver(session.page, inv), session.navigation);
    botPipeline = createBotPipeline(inv, transcript, {
      // When recording, every STT round-trip is teed to <session>.stt.jsonl (the capture/STT/assembly
      // bisect); every settled one also tells the deaf-lane guard the lane is alive.
      transcribe: deafLane.watch(signalRecorder
        ? wrapTranscribeWithTap(createTranscribe(inv, languageControl), signalRecorder.path)
        : createTranscribe(inv, languageControl)),
      languageControl,
      config: speakerStreamConfig,
      // Every STT fault is counted and carried out on the terminal lifecycle event (see
      // sttFaults). Logging it here as well keeps the raw line for anyone tailing the container.
      onError: (e) => { sttFaults.record(e); console.error(`[bot] pipeline fault: ${String(e)}`); },
      // The lane's own observations — today, WHICH TURN SPINE it is running on and why that
      // changed. A transcript carries no trace of that, so without this line a stored fixture
      // cannot say whether it is scoring the transport anchor or the fallback that replaced it.
      onObservation: (source, obs, tMs) => {
        try {
          signalRecorder?.sink.captureObservation?.({
            type: 'observation', t: tMs ?? Date.now(), source, lane: 'mixed', observation: obs,
          });
        } catch { /* an observation must never disturb the meeting */ }
      },
    });
    // Defer the page-side capture start to pipeline.start(): the orchestrator calls it AFTER
    // admission (orchestrator.ts:125), on the LIVE meeting page — where addInitScript has injected
    // window.VexaBrowserUtils and the participant <audio> elements exist. Starting it at launch ran
    // the page.evaluate on the BLANK pre-navigation page (no VexaBrowserUtils, no audio), and the
    // subsequent goto to the meeting URL destroyed that context — so capture never attached. (L4.)
    const sess = session, bp = botPipeline, rec = recording;
    restartCapture = () => {
      void restartMixedCapture(sess.page)
        .then((requested) => console.log(`[bot] capture restart ${requested ? 'requested' : 'not applicable (no mixed rescan)'}`))
        .catch((e) => console.error(`[bot] capture restart failed: ${String(e)}`));
    };
    // In-meeting chat (google_meet + jitsi readers) → a transcript.v1 `chat` segment: the sender is the
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
      startCapture: () => startCaptureBridge(sess.page, inv, bp, signalRecorder?.sink, publishChat, remoteAudioActivity),   // on the live meeting page
      startRecording: rec ? () => startRecording(sess.page, inv, rec) : undefined,          // MediaRecorder → recording.v1
      engine: bp,
      onFault: (stage, e) => {
        console.error(`[bot] live-pipeline: ${stage} failed (non-fatal, bot stays seated): ${serr(e)}`);
      },
    });
    // Tee acts: `speak`/`speak_stop` reach the SpeakController (gated on voiceAgentEnabled),
    // `chat_send` reaches the ChatSender (ungated: a text act, not a voice one) and its outcome goes
    // back to meeting-api on the signed stream, and `reconfigure` reaches the transcription-language
    // control.
    const inMeeting = createActDispatch({
      speak: createSpeakController(session.page, inv),
      chat: createChatSender(session.page, inv),
      report: (r) => liveTranscript.chatSendResult(r),
    });
    acts = teeActs(liveActs, { 'in-meeting': inMeeting, 'transcription-language': languageActs });
  } catch (e) {
    console.error(`[bot] browser launch/capture wiring failed — falling back to clean terminal failed: ${String(e)}`);
    join = noBrowserJoinDriver(String(e));
    pipeline = noBrowserPipeline();
    // No lane runs, so a reconfigure only updates a setting nothing reads — still validated and
    // logged, so the act is never silently dropped.
    acts = teeActs(liveActs, { 'transcription-language': languageActs });
  }

  // Reachability gate (#530): only meaningful under a control plane (a callback URL is set). When
  // present, the orchestrator makes the first `joining` emit load-bearing and, if the callback is
  // unreachable, probes redis before refusing to join. Self-host (no callback) has no gate.
  const reachability = inv.meetingApiCallbackUrl
    ? { probeSecondary: () => pingRedis(inv.redisUrl) }
    : undefined;

  // A voice note is never "alone" by silence (a pause is not a departure): its only lonely end is the
  // source's idle timeout, which fires the orchestrator's own left_alone path through this hook.
  const voiceAloneness = {
    onAlone(cb: () => void) { aloneHook = cb; return () => { aloneHook = null; }; },
  };
  const orchestrator = createOrchestrator(inv, {
    lifecycle,
    join,
    pipeline,
    acts,
    aloneness: noBrowser ? voiceAloneness : aloneness,
    recording: recording as RecordingSink | undefined,
    reachability,
    // What degraded the meeting, on the terminal event: the STT faults AND every stretch of audio a
    // lane will never transcribe (prod-cutover R4: a loss is a recorded gap, never silent).
    degraded: () => {
      const ranges = botPipeline?.missingRanges?.() ?? [];
      const line = describeGaps(ranges);
      if (line) console.error(line);
      return mergeDegraded(sttFaults.report(), transcriptGapsReport(ranges));
    },
  });

  // Disposability (P7): a termination signal ends the active phase gracefully (leave → flush →
  // terminal callback → exit 0) so the container never hangs after `active` — BOUNDED by the
  // force-exit watchdog in signals.ts (<25s, inside the runtime's SIGTERM→SIGKILL stop grace) so
  // a wedged teardown can never ride a `docker stop` all the way to a silent 137 (the incident's
  // exit code on BOTH orphaned bots). Wire before run(); release the listeners after.
  endVoiceNote = (reason) => {
    if (reason === 'left_alone') aloneHook?.();
    else orchestrator.stop('stopped');
  };
  const releaseSignals = installSignalHandlers({ stop: (reason) => orchestrator.stop(reason) });
  try {
    const maxActiveMs = isVoiceNote ? voiceNoteMaxActiveMs(inv)
      : isCall ? callMaxActiveMs(inv) : deriveMaxActiveMs(inv, aloneSilenceWindowMs, env);
    const result = await orchestrator.run({ maxActiveMs });
    // Per speaker: frames and seconds of audio the room delivered (the E2E reads this line).
    if (callStats) console.log(`[bot] call: speakers ${JSON.stringify(callStats())}`);
    return result.exitCode;
  } finally {
    // The session is over (its terminal callback is sent): a Redis auth failure from here on is
    // the revoked credential, and ends the client instead of looping (adapters/redis-terminal.ts).
    redisSession.end();
    releaseSignals();
    stopDeafLane();
    // Tear down the pipeline (capture bridge + recording + engine) + browser (best-effort — a
    // teardown failure must not change the exit code). The orchestrator already stopped the pipeline
    // on a normal end; createLivePipeline.stop() is idempotent, and this also covers an early-exit
    // path that skipped the orchestrator's teardown. (#593)
    await pipeline.stop().catch(() => { /* best-effort */ });
    await signalRecorder?.close().catch(() => { /* best-effort */ });
    // The two teardown sidecars, written BEFORE the upload reads the directory. Both are
    // best-effort by construction: a diagnostic that can change how a meeting ended is worse than
    // no diagnostic. The log tap is released first so the snapshot's own line lands in the file.
    await snapshot?.writeSnapshot().catch(() => { /* best-effort */ });
    botLog?.stop();
    // O-TEL-1: ship the tape AFTER close() (the file is flushed and finalized there) and BEFORE the
    // process exits, because the container's disk dies with it. Best-effort by construction —
    // uploadSignalTapes never throws and never rejects, so nothing here can revise the exit code the
    // orchestrator already returned. Runs inside the 30s SIGTERM grace: one attempt, streamed, with
    // its own timeout, so a slow object store cannot ride the grace all the way to a SIGKILL.
    await uploadSignalTapes(signalRecorder, { inv });
    if (session) await session.close().catch(() => { /* best-effort */ });
    // Quit the redis connections on teardown (best-effort — a quit failure must not change the
    // exit code; they may never have connected if redis was unreachable).
    await transcriptClient.quit().catch(() => { /* best-effort */ });
    await actsClient.quit().catch(() => { /* best-effort */ });
    await voiceStreamClient?.quit().catch(() => { /* best-effort */ });
  }
}

// Worker entrypoint: boot, work, emit, die (P7).
if (import.meta.url === `file://${process.argv[1]}`) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e); process.exit(1); });
}
