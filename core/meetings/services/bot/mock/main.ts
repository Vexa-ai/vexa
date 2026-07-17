/**
 * The MOCK BOT composition root (Lane A) — the worker entrypoint baked into `mock-bot:dev`.
 *
 * Mirrors the real bot's `src/index.ts` wiring, but swaps ONLY the two heavy ports for the scenario
 * fakes (`fakeJoinDriver` / `fakePipeline`). Everything the BACKEND observes is the REAL thing:
 *   • lifecycle.v1 → the REAL HTTP sink → meeting-api's callback (the FSM under test)
 *   • transcript.v1 → the REAL redis stream → the collector → postgres → /ws
 *   • acts.v1 → the REAL redis subscription (a `leave` ends the run; a `speak` is acked)
 *   • recording → a real multipart chunk POSTed to the backend's /internal/recordings/upload
 * So this proves the control plane at L3 with no browser/STT/GPU. Scenario via env `MOCK_SCENARIO`.
 */
import { loadInvocation, InvocationError, type Invocation } from '../src/config.js';
import type { Act, LifecycleEvent } from '../src/contracts.js';
import { createOrchestrator } from '../src/orchestrator.js';
import { createHttpLifecycleSink } from '../src/adapters/lifecycle-http.js';
import { createRedisTranscriptSink, redisClientFrom } from '../src/adapters/transcript-redis.js';
import { createRedisActsSource, redisActsClientFrom } from '../src/adapters/acts-redis.js';
import type { ActsSource, LifecycleSink, TranscriptSink } from '../src/ports.js';
import { getScenario, fakeJoinDriver, fakePipeline, mockSegment } from './scenarios.js';
import { createRemoteAudioActivityTap, createSilenceAlonenessSource, resolveAloneSilenceWindowMs } from '../src/aloneness.js';

function consoleLifecycleSink(): LifecycleSink {
  return { async emit(e: LifecycleEvent) { console.log(`[mock] lifecycle.v1 ${e.status}${e.completion_reason ? ` (${e.completion_reason})` : ''}${e.failure_stage ? ` @${e.failure_stage}` : ''}`); } };
}

function meetingChannelId(inv: Invocation): string | number {
  return inv.meeting_id ?? inv.nativeMeetingId ?? inv.connectionId ?? 'session';
}

/** A canonical 16-bit PCM mono WAV (RIFF) chunk — the same shape the backend's recording receiver
 *  expects (mirrors deploy/compose/tests' `_canonical_wav`). */
function canonicalWav(samples = 8000): Uint8Array {
  const dataLen = samples * 2;
  const buf = Buffer.alloc(44 + dataLen);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + dataLen, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(16000, 24); buf.writeUInt32LE(32000, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(dataLen, 40);
  return new Uint8Array(buf);
}

/** POST one recording chunk to the backend's receiver (the recording leg of the mock). Endpoint:
 *  inv.recordingUploadUrl, else derived from the meeting-api callback origin. Auth: the MeetingToken. */
async function uploadMockChunk(inv: Invocation, log: (m: string) => void): Promise<void> {
  let endpoint = inv.recordingUploadUrl;
  if (!endpoint && inv.meetingApiCallbackUrl) endpoint = new URL(inv.meetingApiCallbackUrl).origin + '/internal/recordings/upload';
  if (!endpoint) { log('recording: no upload endpoint (skipping chunk)'); return; }
  const form = new FormData();
  form.set('session_uid', inv.connectionId ?? 'sess');
  form.set('media_type', 'audio');
  form.set('media_format', 'wav');
  form.set('chunk_seq', '0');
  form.set('is_final', 'true');
  form.set('file', new Blob([canonicalWav()], { type: 'audio/wav' }), '000000.wav');
  const headers: Record<string, string> = {};
  if (inv.token) headers['Authorization'] = `Bearer ${inv.token}`;
  const res = await fetch(endpoint, { method: 'POST', headers, body: form });
  log(`recording: chunk → ${endpoint} → ${res.status}`);
  if (!res.ok) throw new Error(`recording upload ${res.status}`);
}

/** Tee the live acts source so a `speak` act publishes a marker transcript segment (proves the
 *  acts.v1 round-trip end-to-end through the backend), while `leave` still reaches the orchestrator. */
function teeSpeak(source: ActsSource, transcript: TranscriptSink, inv: Invocation): ActsSource {
  return {
    subscribe(handler) {
      return source.subscribe((act: Act) => {
        void Promise.resolve(handler(act)).catch(() => {});
        if (act.action === 'speak') void transcript.publish(mockSegment(inv, 999, `[mock spoke: ${act.text ?? ''}]`)).catch(() => {});
      });
    },
  };
}

export async function main(env: NodeJS.ProcessEnv = process.env): Promise<number> {
  let inv: Invocation;
  try { inv = loadInvocation(env); }
  catch (e) {
    if (e instanceof InvocationError) {
      console.error(`[mock] FATAL ${e.message}`);
      await consoleLifecycleSink().emit({ connection_id: env.VEXA_CONNECTION_ID ?? '', status: 'failed', failure_stage: 'requested', completion_reason: 'validation_error', reason: e.message, exit_code: 1 }).catch(() => {});
      return 1;
    }
    throw e;
  }
  // Scenario seam (NO contract change): env MOCK_SCENARIO wins; else decode botName "mock:<scenario>"
  // (botName is already in invocation.v1, so the compose test selects a behaviour via bot_name).
  const fromName = inv.botName?.startsWith('mock:') ? inv.botName.slice('mock:'.length) : undefined;
  const scenario = getScenario(env.MOCK_SCENARIO ?? fromName);
  console.log(`[mock] scenario=${scenario.name} meeting_id=${inv.meeting_id ?? '?'} conn=${inv.connectionId ?? '?'}`);

  const meetingId = meetingChannelId(inv);
  const lifecycle: LifecycleSink = inv.meetingApiCallbackUrl
    ? createHttpLifecycleSink({ callbackUrl: inv.meetingApiCallbackUrl, internalSecret: inv.internalSecret })
    : consoleLifecycleSink();
  const transcriptClient = redisClientFrom(inv.redisUrl);
  const actsClient = redisActsClientFrom(inv.redisUrl);
  const transcript: TranscriptSink = createRedisTranscriptSink({ client: transcriptClient, meetingId });
  const liveActs = createRedisActsSource({ client: actsClient, meetingId });

  let stopRef: (r: 'stopped') => void = () => {};
  const wantRecording = scenario.recording || inv.recordingEnabled;
  const join = fakeJoinDriver(scenario);
  const pipeline = fakePipeline(scenario, inv, transcript, {
    endRun: (r) => stopRef(r as 'stopped'),
    recordChunk: wantRecording ? () => uploadMockChunk(inv, (m) => console.log(`[mock] ${m}`)) : undefined,
    log: (m) => console.log(`[mock] ${m}`),
  });
  const acts: ActsSource = teeSpeak(liveActs, transcript, inv);

  const activity = createRemoteAudioActivityTap();
  const aloneness = scenario.silenceAlone
    ? createSilenceAlonenessSource({
        activity,
        windowMs: resolveAloneSilenceWindowMs(inv.automaticLeave?.everyoneLeftTimeout, env),
        log: (message) => console.log(`[mock] ${message}`),
      })
    : { onAlone() { return () => {}; } };
  if (scenario.silenceAlone) activity.ready();
  const orchestrator = createOrchestrator(inv, { lifecycle, join, pipeline, acts, aloneness });
  stopRef = orchestrator.stop;

  const onSignal = () => orchestrator.stop('stopped');
  process.once('SIGTERM', onSignal);
  process.once('SIGINT', onSignal);
  try {
    const result = await orchestrator.run({ maxActiveMs: 0 });   // mock self-ends per scenario or via backend leave/SIGTERM
    return result.exitCode;
  } finally {
    process.off('SIGTERM', onSignal);
    process.off('SIGINT', onSignal);
    await transcriptClient.quit().catch(() => {});
    await actsClient.quit().catch(() => {});
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e); process.exit(1); });
}
