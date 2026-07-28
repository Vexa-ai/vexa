/**
 * ALLOY: Adapter-level proof for pause-bounded automatic language re-detection.
 *
 * Break caught: ignoring the opt-in, sending a language pin, running child requests in parallel,
 * or merging child-local timestamps without offsets would lose a language leg or corrupt stt.v1.
 *
 * Run: pnpm --filter @vexa/transcribe-whisper exec tsx src/auto-language.test.ts
 */
import {
  TranscriptionClient,
  TranscriptionError,
  type TranscriptionExecutionObserver,
} from './index.js';

let failed = 0;
const check = (name: string, condition: boolean, detail = ''): void => {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : ` — ${detail}`}`);
  if (!condition) failed++;
};

const pcm = new Float32Array(64_000);
pcm.fill(0.2, 0, 16_000);
pcm.fill(0.2, 24_000, 40_000);
pcm.fill(0.2, 48_000, 64_000);

const serviceResponses = [
  {
    text: 'Hello',
    language: 'en',
    language_probability: 0.93,
    duration: 1.25,
    segments: [{
      start: 0.1,
      end: 0.8,
      text: 'Hello',
      avg_logprob: -0.1,
      no_speech_prob: 0.01,
      compression_ratio: 1,
      words: [{ word: 'Hello', start: 0.1, end: 0.8, probability: 0.95 }],
    }],
  },
  {
    text: 'Привет',
    language: 'ru',
    language_probability: 0.91,
    duration: 1.5,
    segments: [{
      start: 0.2,
      end: 0.8,
      text: 'Привет',
      avg_logprob: -0.1,
      no_speech_prob: 0.01,
      compression_ratio: 1,
      words: [{ word: 'Привет', start: 0.2, end: 0.8, probability: 0.94 }],
    }],
  },
  {
    text: 'again',
    language: 'en',
    language_probability: 0.92,
    duration: 1.25,
    segments: [{
      start: 0.05,
      end: 0.65,
      text: 'again',
      avg_logprob: -0.1,
      no_speech_prob: 0.01,
      compression_ratio: 1,
      words: [{ word: 'again', start: 0.05, end: 0.65, probability: 0.96 }],
    }],
  },
];

const realFetch = globalThis.fetch;
const formPart = (body: string, name: string): string | null =>
  body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)\\r\\n`))?.[1] ?? null;
const bodies: string[] = [];
let activeFetches = 0;
let maxActiveFetches = 0;
(globalThis as any).fetch = async (_url: unknown, init: { body: Buffer }) => {
  const response = serviceResponses[bodies.length];
  bodies.push(Buffer.from(init.body).toString('latin1'));
  activeFetches++;
  maxActiveFetches = Math.max(maxActiveFetches, activeFetches);
  await Promise.resolve();
  activeFetches--;
  return new Response(JSON.stringify(response), { status: 200 });
};

const observerEvents: string[] = [];
const observer: TranscriptionExecutionObserver = {
  waiting: () => observerEvents.push('waiting'),
  started: () => observerEvents.push('started'),
  finished: () => observerEvents.push('finished'),
};

try {
  const client = new TranscriptionClient({
    serviceUrl: 'http://stt.test',
    maxRetries: 0,
    sampleRate: 16_000,
    autoDetectLanguagePerSegment: true,
  });
  const result = await client.transcribe(pcm, undefined, 'previous context', observer);

  check('one pause-bounded request per spoken leg', bodies.length === 3, `got ${bodies.length}`);
  check(
    'child requests are sequential',
    maxActiveFetches === 1,
    `max active fetches ${maxActiveFetches}`,
  );
  check(
    'no child request pins language',
    bodies.length === 3 && bodies.every((body) => formPart(body, 'language') === null),
    JSON.stringify(bodies.map((body) => formPart(body, 'language'))),
  );
  check(
    'only the first child inherits prior context',
    formPart(bodies[0] ?? '', 'prompt') === 'previous context'
      && formPart(bodies[1] ?? '', 'prompt') === null
      && formPart(bodies[2] ?? '', 'prompt') === null,
    JSON.stringify(bodies.map((body) => formPart(body, 'prompt'))),
  );
  check(
    'text preserves EN → RU → EN order',
    result.text === 'Hello Привет again',
    JSON.stringify(result.text),
  );
  check('mixed child languages aggregate to mul', result.language === 'mul', result.language);
  check(
    'mixed aggregate does not overstate one language probability',
    result.language_probability === undefined,
    String(result.language_probability),
  );
  check('duration is the original four-second PCM window', result.duration === 4, String(result.duration));
  check(
    'segment offsets are relative to the original window',
    JSON.stringify(result.segments.map(({ start, end }) => ({ start, end })))
      === JSON.stringify([
        { start: 0.1, end: 0.8 },
        { start: 1.45, end: 2.05 },
        { start: 2.8, end: 3.4 },
      ]),
    JSON.stringify(result.segments),
  );
  check(
    'word offsets are relative to the original window',
    JSON.stringify(result.segments.map((segment) => segment.words?.[0] && ({
      start: segment.words[0].start,
      end: segment.words[0].end,
    }))) === JSON.stringify([
      { start: 0.1, end: 0.8 },
      { start: 1.45, end: 2.05 },
      { start: 2.8, end: 3.4 },
    ]),
    JSON.stringify(result.segments),
  );
  check(
    'one logical request reports one observer lifecycle',
    observerEvents.join(',') === 'started,finished',
    observerEvents.join(','),
  );
} finally {
  (globalThis as any).fetch = realFetch;
}

{
  let calls = 0;
  const probabilities = [0.89, 0.71, 0.83];
  try {
    (globalThis as any).fetch = async () => {
      const index = calls++;
      return new Response(JSON.stringify({
        text: ['Alpha', 'Beta', 'Gamma'][index],
        language: 'en',
        language_probability: probabilities[index],
        duration: [1.25, 1.5, 1.25][index],
        segments: [],
      }), { status: 200 });
    };
    const result = await new TranscriptionClient({
      serviceUrl: 'http://stt.test',
      maxRetries: 0,
      sampleRate: 16_000,
      autoDetectLanguagePerSegment: true,
    }).transcribe(pcm);
    check('agreed child languages stay specific', result.language === 'en', result.language);
    check(
      'agreed language confidence uses the conservative minimum',
      result.language_probability === 0.71,
      String(result.language_probability),
    );
  } finally {
    (globalThis as any).fetch = realFetch;
  }
}

{
  let calls = 0;
  let fault: unknown;
  try {
    (globalThis as any).fetch = async () => {
      calls++;
      if (calls === 2) return new Response('backend unavailable', { status: 503 });
      return new Response(JSON.stringify(serviceResponses[0]), { status: 200 });
    };
    try {
      await new TranscriptionClient({
        serviceUrl: 'http://stt.test',
        maxRetries: 0,
        sampleRate: 16_000,
        autoDetectLanguagePerSegment: true,
      }).transcribe(pcm);
    } catch (error) {
      fault = error;
    }
    check(
      'a child failure rejects the whole logical result',
      fault instanceof TranscriptionError && fault.kind === 'unavailable',
      String(fault),
    );
    check('no later child runs after the failure', calls === 2, `got ${calls} calls`);
  } finally {
    (globalThis as any).fetch = realFetch;
  }
}

{
  let calls = 0;
  try {
    (globalThis as any).fetch = async () => {
      calls++;
      return new Response(JSON.stringify(serviceResponses[0]), { status: 200 });
    };
    await new TranscriptionClient({
      serviceUrl: 'http://stt.test',
      maxRetries: 0,
      sampleRate: 16_000,
      autoDetectLanguagePerSegment: true,
    }).transcribe(new Float32Array(16_000).fill(0.2));
    check('auto mode without a qualifying pause sends one request', calls === 1, `got ${calls}`);
  } finally {
    (globalThis as any).fetch = realFetch;
  }
}

{
  const disabledBodies: string[] = [];
  try {
    (globalThis as any).fetch = async (_url: unknown, init: { body: Buffer }) => {
      disabledBodies.push(Buffer.from(init.body).toString('latin1'));
      return new Response(JSON.stringify(serviceResponses[1]), { status: 200 });
    };
    await new TranscriptionClient({
      serviceUrl: 'http://stt.test',
      maxRetries: 0,
      sampleRate: 16_000,
    }).transcribe(pcm, 'ru', 'configured context');
    check(
      'disabled opt-in preserves one configured-language request',
      disabledBodies.length === 1
        && formPart(disabledBodies[0], 'language') === 'ru'
        && formPart(disabledBodies[0], 'prompt') === 'configured context',
      JSON.stringify(disabledBodies.map((body) => ({
        language: formPart(body, 'language'),
        prompt: formPart(body, 'prompt'),
      }))),
    );
  } finally {
    (globalThis as any).fetch = realFetch;
  }
}

if (failed) {
  console.error(`\n❌ auto language: ${failed} check(s) failed.`);
  process.exit(1);
}
console.log('\n✅ auto language: pause-bounded requests merge into one honest STT result.');
