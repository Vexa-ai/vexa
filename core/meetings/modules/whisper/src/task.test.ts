/**
 * B3 (#516 C3): the `task` form part on the STT wire. The transcription service reads
 * `task: str = Form("transcribe")` and hands it to the model, so `translate` is the difference
 * between foreign speech transcribed in its own language and the same speech rendered as English
 * text. The client had no parameter for it, so the field could never leave the bot.
 *
 * Asserts the two halves that matter: set ⇒ the part is on the wire with that value; unset ⇒ the
 * part is ABSENT (the back-compatible wire — the service's own default applies, byte-for-byte the
 * request we sent before this change).
 * Run: npm test (chained)  or  npx tsx src/task.test.ts
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
/** The value of a named form part in a captured multipart body (null if the part is absent). */
function partOf(body: string, name: string): string | null {
  const m = body.match(new RegExp(`name="${name}"\r\n\r\n([^\r]*)\r\n`));
  return m ? m[1] : null;
}

async function run() {
  const pcm = new Float32Array(1600).fill(0.05); // 0.1s of audio

  // task=translate → the wire carries it beside language.
  {
    const body = captureFetch();
    const client = new TranscriptionClient({ serviceUrl: 'http://stt.test' });
    await client.transcribe(pcm, 'es', undefined, 'translate');
    check('task=translate rides the task form part', partOf(body(), 'task') === 'translate', `got ${JSON.stringify(partOf(body(), 'task'))}`);
    check('language still rides beside it', partOf(body(), 'language') === 'es', `got ${JSON.stringify(partOf(body(), 'language'))}`);
  }

  // task=transcribe → explicit, still on the wire (the reconfigure that switches BACK).
  {
    const body = captureFetch();
    const client = new TranscriptionClient({ serviceUrl: 'http://stt.test' });
    await client.transcribe(pcm, 'es', undefined, 'transcribe');
    check('task=transcribe rides the task form part', partOf(body(), 'task') === 'transcribe', `got ${JSON.stringify(partOf(body(), 'task'))}`);
  }

  // Unset → the part is absent: the pre-change wire, so the service default governs.
  {
    const body = captureFetch();
    const client = new TranscriptionClient({ serviceUrl: 'http://stt.test' });
    await client.transcribe(pcm, 'en');
    check('unset → no task part (back-compatible wire)', partOf(body(), 'task') === null, `got ${JSON.stringify(partOf(body(), 'task'))}`);
  }

  (globalThis as any).fetch = realFetch;
  if (failed) { console.error(`\n❌ stt task: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ stt task (#516 C3): `task` reaches the STT service; omitting it keeps the old wire.');
}
run().catch((e) => { console.error(e); process.exit(1); });
