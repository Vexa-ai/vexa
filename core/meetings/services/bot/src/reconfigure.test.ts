/**
 * B2 (#516 C2) — a RUNNING bot's transcription language/task changes mid-meeting.
 *
 * The claim under test is not "the handler was called". It is "a `reconfigure` published on the
 * meeting's command bus changes what the NEXT STT request puts on the wire" — so the chain is
 * driven end to end, with only redis and the STT server stubbed:
 *
 *   raw JSON on `bot_commands:meeting:{id}`
 *     → the REAL createRedisActsSource (fake client) → the REAL parseAct
 *     → the REAL composition-root tee (`teeActs`) + `configHandler` from index.ts
 *     → the SttConfigRef box
 *     → the REAL createTranscribe closure → the REAL TranscriptionClient
 *     → the multipart body a stubbed global fetch captures.
 *
 * Asserts:
 *   • before the act, every request carries the spawn-time language and no task;
 *   • the request AFTER the act carries the new language (and task) — the flip happens exactly at
 *     the act boundary, once, and every later request stays flipped;
 *   • an acts.v1 GOLDEN `Act.reconfigure.json` (the published contract sample) drives it — not a
 *     shape invented by this test;
 *   • `language: null` clears back to model-detect (the contract types it `string | null`);
 *   • the apply is LOUD: one old→new line on the console (a silent config change is unauditable);
 *   • an act that changes nothing is still reported, and never disturbs the wire;
 *   • the orchestrator still sees every act through the tee (`leave` is not swallowed by it).
 *
 * Run: npx tsx src/reconfigure.test.ts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRedisActsSource, type RedisActsClient } from './adapters/acts-redis.js';
import { configHandler, teeActs } from './index.js';
import { createSttConfigRef, createTranscribe } from './pipeline.js';
import type { Invocation } from './config.js';
import type { Act } from './contracts.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const HERE = dirname(fileURLToPath(import.meta.url));
const ACTS_GOLDEN = join(HERE, '..', '..', '..', 'contracts', 'acts.v1', 'golden');
const golden = (n: string): string => readFileSync(join(ACTS_GOLDEN, n), 'utf8');

/** A fake redis subscriber: captures the listener so the test can PUBLISH raw messages. */
function fakeActsClient() {
  let listener: ((m: string) => void) | undefined;
  const client: RedisActsClient = {
    subscribe(_channel, cb) { listener = cb; },
    unsubscribe() { /* no-op */ },
  };
  return { client, publish: (m: string) => listener?.(m) };
}

/** One captured STT request: the form parts that carry the config. */
interface WireRequest { language: string | null; task: string | null }

const realFetch = globalThis.fetch;
const realLog = console.log;

function partOf(body: string, name: string): string | null {
  const m = body.match(new RegExp(`name="${name}"\r\n\r\n([^\r]*)\r\n`));
  return m ? m[1] : null;
}

/** Stub global fetch with a 200 STT reply and record each request's language/task parts. */
function captureWire(): WireRequest[] {
  const seen: WireRequest[] = [];
  (globalThis as any).fetch = async (_url: unknown, init: { body: Buffer }) => {
    const body = Buffer.from(init.body).toString('latin1');
    seen.push({ language: partOf(body, 'language'), task: partOf(body, 'task') });
    return new Response(
      JSON.stringify({ text: 'ok', language: 'en', duration: 0.1, segments: [] }),
      { status: 200 },
    );
  };
  return seen;
}

const inv = (over: Partial<Invocation> = {}): Invocation => ({
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'Vexa',
  redisUrl: 'redis://localhost:6379', transcribeEnabled: true,
  transcriptionServiceUrl: 'http://stt.test', language: 'en', ...over,
});

const PCM = new Float32Array(1600).fill(0.05); // 0.1s of audio

async function main(): Promise<void> {
  // ── 1) The whole chain: a golden reconfigure on the bus flips the next STT request ──
  {
    const wire = captureWire();
    const logs: string[] = [];
    console.log = (...a: unknown[]) => { logs.push(a.join(' ')); };

    const sttConfig = createSttConfigRef({ language: inv().language ?? undefined });
    const transcribe = createTranscribe(inv(), sttConfig);
    const bus = fakeActsClient();
    const orchestratorSaw: Act[] = [];
    const acts = teeActs(
      createRedisActsSource({ client: bus.client, meetingId: 42 }),
      configHandler(sttConfig),
    );
    acts.subscribe((act) => { orchestratorSaw.push(act); });

    await transcribe(PCM);                       // spawn-time config
    await transcribe(PCM);
    bus.publish(golden('Act.reconfigure.json')); // {"action":"reconfigure","language":"es","task":"transcribe"}
    await transcribe(PCM);                       // must carry the NEW config
    await transcribe(PCM);

    console.log = realLog;

    check('before the act: spawn language on the wire', wire[0]?.language === 'en' && wire[1]?.language === 'en', JSON.stringify(wire.slice(0, 2)));
    check('before the act: no task on the wire', wire[0]?.task === null && wire[1]?.task === null, JSON.stringify(wire.slice(0, 2)));
    check('AFTER the act: the next request carries language=es', wire[2]?.language === 'es', JSON.stringify(wire[2]));
    check('AFTER the act: the next request carries task=transcribe', wire[2]?.task === 'transcribe', JSON.stringify(wire[2]));
    check('the flip STICKS for every later request', wire[3]?.language === 'es' && wire[3]?.task === 'transcribe', JSON.stringify(wire[3]));
    check('the flip happens exactly ONCE, at the act boundary',
      wire.filter((r) => r.language === 'en').length === 2 && wire.filter((r) => r.language === 'es').length === 2,
      JSON.stringify(wire));
    check('the apply is LOUD: one old→new line naming both configs',
      logs.some((l) => l.includes('[bot] reconfigure:') && l.includes('language=en') && l.includes('language=es')),
      JSON.stringify(logs));
    check('the orchestrator still receives the act through the tee',
      orchestratorSaw.length === 1 && orchestratorSaw[0]?.action === 'reconfigure',
      JSON.stringify(orchestratorSaw));
  }

  // ── 2) task=translate mid-call (V2): the field the docs promise reaches the service ──
  {
    const wire = captureWire();
    console.log = () => { /* quiet */ };
    const sttConfig = createSttConfigRef({ language: 'en' });
    const transcribe = createTranscribe(inv(), sttConfig);
    const bus = fakeActsClient();
    teeActs(createRedisActsSource({ client: bus.client, meetingId: 7 }), configHandler(sttConfig)).subscribe(() => {});

    await transcribe(PCM);
    bus.publish(JSON.stringify({ action: 'reconfigure', task: 'translate' }));
    await transcribe(PCM);
    console.log = realLog;

    check('task=translate reaches the wire mid-call', wire[1]?.task === 'translate', JSON.stringify(wire[1]));
    check('an unnamed field is LEFT ALONE (language survives a task-only act)', wire[1]?.language === 'en', JSON.stringify(wire[1]));
  }

  // ── 3) `language: null` clears back to model-detect (the contract types it string|null) ──
  {
    const wire = captureWire();
    console.log = () => { /* quiet */ };
    const sttConfig = createSttConfigRef({ language: 'en' });
    const transcribe = createTranscribe(inv(), sttConfig);
    const bus = fakeActsClient();
    teeActs(createRedisActsSource({ client: bus.client, meetingId: 7 }), configHandler(sttConfig)).subscribe(() => {});

    await transcribe(PCM);
    bus.publish(JSON.stringify({ action: 'reconfigure', language: null }));
    await transcribe(PCM);
    console.log = realLog;

    check('language:null clears the pin — no language part on the wire', wire[1]?.language === null, JSON.stringify(wire[1]));
  }

  // ── 4) A no-op act is reported, and never disturbs the wire ──
  {
    const wire = captureWire();
    const logs: string[] = [];
    console.log = (...a: unknown[]) => { logs.push(a.join(' ')); };
    const sttConfig = createSttConfigRef({ language: 'en' });
    const transcribe = createTranscribe(inv(), sttConfig);
    const bus = fakeActsClient();
    teeActs(createRedisActsSource({ client: bus.client, meetingId: 7 }), configHandler(sttConfig)).subscribe(() => {});

    bus.publish(JSON.stringify({ action: 'reconfigure', language: 'en' }));
    await transcribe(PCM);
    console.log = realLog;

    check('a no-op reconfigure still logs (never silent)', logs.some((l) => l.includes('no change')), JSON.stringify(logs));
    check('a no-op reconfigure leaves the wire untouched', wire[0]?.language === 'en', JSON.stringify(wire[0]));
  }

  // ── 5) The tee does not swallow the orchestrator's own act (leave still lands) ──
  {
    console.log = () => { /* quiet */ };
    const sttConfig = createSttConfigRef({ language: 'en' });
    const bus = fakeActsClient();
    const seen: Act[] = [];
    teeActs(createRedisActsSource({ client: bus.client, meetingId: 7 }), configHandler(sttConfig))
      .subscribe((act) => { seen.push(act); });
    bus.publish(golden('Act.leave.json'));
    console.log = realLog;
    check('leave still reaches the orchestrator handler', seen.length === 1 && seen[0]?.action === 'leave', JSON.stringify(seen));
  }

  (globalThis as any).fetch = realFetch;
  console.log = realLog;
  if (failed) { console.error(`\n❌ reconfigure: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ reconfigure (#516 C2): a reconfigure act on the bus changes the RUNNING bot\'s next STT request.');
}

main().catch((e) => { console.error(e); process.exit(1); });
