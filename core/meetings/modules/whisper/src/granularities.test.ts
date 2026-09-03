/**
 * P5 gate (the #1349 fixture the STT seam lacked): the request the wire actually sees names the
 * timestamp granularities the way the OpenAI audio API defines them — an ARRAY, so
 * `timestamp_granularities[]`. Groq's /openai/v1/audio/transcriptions validates the schema and
 * answers 400 `unknown param \`timestamp_granularities\`` to the unbracketed name, so every live
 * segment of a Groq-pointed deployment fails until the adapter speaks the array spelling.
 * `segment` is asserted alongside `word` because Groq answers `segments: null` when only `word` is
 * asked for, and the client reads `data.segments` — a bracket-only fix trades a loud 400 for a
 * silent loss of every per-segment timing and confidence. Stubs global fetch and inspects the
 * multipart body ("validating backend" edge — D-A2).
 * Run: npm test (chained)  or  npx tsx src/granularities.test.ts
 */
import { TranscriptionClient } from './index.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const realFetch = globalThis.fetch;
/** Replace global fetch with a 200 stub that CAPTURES the multipart body. */
function captureFetch(): () => string {
  let body = '';
  (globalThis as any).fetch = async (_url: unknown, init: { body: Buffer }) => {
    body = Buffer.from(init.body).toString('latin1');
    return new Response(JSON.stringify({ text: 'ok', language: 'en', duration: 0.1, segments: [] }), { status: 200 });
  };
  return () => body;
}
/** Every value sent under the given form-part name, in wire order. */
function partValues(body: string, name: string): string[] {
  const escaped = name.replace(/[[\]]/g, '\\$&');
  return [...body.matchAll(new RegExp(`name="${escaped}"\\r\\n\\r\\n([^\\r]*)\\r\\n`, 'g'))].map((m) => m[1]);
}

async function run() {
  const pcm = new Float32Array(1600).fill(0.05); // 0.1s of audio
  const body = captureFetch();
  const client = new TranscriptionClient({ serviceUrl: 'http://stt.test', model: 'whisper-large-v3-turbo' });
  await client.transcribe(pcm, 'en');
  const wire = body();

  // The array spelling is what a schema-validating backend accepts.
  check('granularities ride the bracketed array name',
    partValues(wire, 'timestamp_granularities[]').length > 0,
    'no timestamp_granularities[] part on the wire');

  // The unbracketed name is what Groq 400s on — it must not be sent at all.
  check('the unbracketed name is never sent',
    partValues(wire, 'timestamp_granularities').length === 0,
    `got ${JSON.stringify(partValues(wire, 'timestamp_granularities'))}`);

  // `word` alone answers segments: null, and the client reads data.segments.
  check('segment is requested alongside word',
    JSON.stringify(partValues(wire, 'timestamp_granularities[]').slice().sort()) === JSON.stringify(['segment', 'word']),
    `got ${JSON.stringify(partValues(wire, 'timestamp_granularities[]'))}`);

  // response_format=verbose_json is the precondition for granularities at all (already sent).
  check('verbose_json still accompanies the granularities',
    partValues(wire, 'response_format')[0] === 'verbose_json',
    `got ${JSON.stringify(partValues(wire, 'response_format'))}`);

  globalThis.fetch = realFetch;
  console.log(failed === 0 ? '\n✅ granularities: all checks passed' : `\n❌ granularities: ${failed} check(s) failed`);
  process.exit(failed === 0 ? 0 : 1);
}

run();
