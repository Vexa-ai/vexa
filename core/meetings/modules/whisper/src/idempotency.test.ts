/**
 * #1344 gate: every request the STT seam puts on the wire carries an `Idempotency-Key` header of
 * 1..255 characters (the hosted transcription API answers 400 without one). One key names one
 * logical request — one audio window — so:
 *   - two windows never share a key (the backend must not dedupe distinct audio);
 *   - a transient-failure retry of the SAME window re-sends the SAME key and the SAME bytes
 *     (the backend can recognise the retry instead of transcribing — and billing — it twice);
 *   - the verbose_json → json renegotiation is a DIFFERENT request body, so it takes a new key.
 * Stubs global fetch and inspects the headers + body the wire sees.
 * Run: npm test (chained)  or  npx tsx src/idempotency.test.ts
 */
import { TranscriptionClient } from './index.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

interface Seen { key: string | undefined; body: string }
const realFetch = globalThis.fetch;
const okResponse = () =>
  new Response(JSON.stringify({ text: 'ok', language: 'en', duration: 0.1, segments: [] }), { status: 200 });

/** Replace global fetch with a scripted stub that records each request's key and body. */
function scriptFetch(answer: (n: number) => Response): Seen[] {
  const seen: Seen[] = [];
  (globalThis as any).fetch = async (_url: unknown, init: { headers: Record<string, string>; body: Buffer }) => {
    seen.push({ key: init.headers['Idempotency-Key'], body: Buffer.from(init.body).toString('latin1') });
    return answer(seen.length);
  };
  return seen;
}
const validKey = (k: string | undefined) => typeof k === 'string' && k.length >= 1 && k.length <= 255;

async function run() {
  const pcm = new Float32Array(1600).fill(0.05); // 0.1s of audio

  // Every request carries a key the hosted API accepts; distinct windows get distinct keys.
  {
    const seen = scriptFetch(okResponse);
    const client = new TranscriptionClient({ serviceUrl: 'http://stt.test' });
    await client.transcribe(pcm, 'en');
    await client.transcribe(pcm, 'en');
    check('request carries an Idempotency-Key of 1..255 chars', validKey(seen[0]?.key), `got ${JSON.stringify(seen[0]?.key)}`);
    check('two windows → two distinct keys', validKey(seen[1]?.key) && seen[0].key !== seen[1].key,
      `keys=${JSON.stringify(seen.map((s) => s.key))}`);
  }

  // A transient failure (503, then a timeout-shaped network error) retries the same request:
  // same key, byte-identical body.
  {
    const seen = scriptFetch((n) => {
      if (n === 1) return new Response('busy', { status: 503 });
      if (n === 2) throw Object.assign(new Error('socket hang up'), { name: 'TypeError' });
      return okResponse();
    });
    const client = new TranscriptionClient({ serviceUrl: 'http://stt.test', maxRetries: 3, retryDelayMs: 1 });
    await client.transcribe(pcm, 'en', 'previous text');
    check('retries happened (3 attempts)', seen.length === 3, `attempts=${seen.length}`);
    check('every retry re-sends the first attempt\'s key',
      validKey(seen[0]?.key) && seen.every((s) => s.key === seen[0].key), `keys=${JSON.stringify(seen.map((s) => s.key))}`);
    check('every retry re-sends byte-identical bytes', seen.every((s) => s.body === seen[0].body),
      'multipart body (boundary included) changed between attempts');
  }

  // Format renegotiation changes the body, so it is a new request with a new key.
  {
    const seen = scriptFetch((n) => n === 1
      ? new Response(JSON.stringify({ message: 'Currently do not support verbose_json for Voxtral' }), { status: 400 })
      : okResponse());
    const client = new TranscriptionClient({ serviceUrl: 'http://stt.test', model: 'voxtral', maxRetries: 0 });
    await client.transcribe(pcm, 'en');
    check('verbose_json → json fallback sends a new key',
      seen.length === 2 && validKey(seen[0].key) && validKey(seen[1].key) && seen[0].key !== seen[1].key,
      `keys=${JSON.stringify(seen.map((s) => s.key))}`);
  }

  (globalThis as any).fetch = realFetch;
  if (failed) { console.error(`\n❌ stt idempotency: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ stt idempotency (#1344): one key per audio window, reused across its retries.');
}
run().catch((e) => { console.error(e); process.exit(1); });
