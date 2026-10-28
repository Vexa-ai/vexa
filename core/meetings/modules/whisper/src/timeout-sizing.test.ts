/**
 * The per-request STT timeout is sized to the window it carries, and a request that outlives it
 * writes ONE log line naming the request and its audio length — never its audio or text.
 *
 * app.dev voice note, 2026-10-13: one request to the remote STT hung while /health answered in
 * ~1 s. With a fixed 90 s timeout the client waited a minute and a half for a window of a few
 * seconds, and nothing in the log said which request was slow or how long it had been given.
 *
 * A local HTTP server stands in for the STT; delays are scaled down so the test runs in ~2 s.
 *   npx tsx src/timeout-sizing.test.ts
 */
import { createServer } from 'node:http';
import { TranscriptionClient, TranscriptionError, sttRequestTimeoutMs } from './index.js';
import { setLogger } from './log.js';

const logs: string[] = [];
setLogger((m) => { logs.push(String(m)); });
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

async function run() {
  // The rule itself: a floor, a multiple of the audio, and the configured timeout as the ceiling.
  check('a 6 s window gets the 30 s floor', sttRequestTimeoutMs(6, { capMs: 90_000 }) === 30_000, String(sttRequestTimeoutMs(6, { capMs: 90_000 })));
  check('a 20 s window gets 3× its length (60 s)', sttRequestTimeoutMs(20, { capMs: 90_000 }) === 60_000, String(sttRequestTimeoutMs(20, { capMs: 90_000 })));
  check('a 45 s window is capped at the configured timeout', sttRequestTimeoutMs(45, { capMs: 90_000 }) === 90_000, String(sttRequestTimeoutMs(45, { capMs: 90_000 })));
  check('a configured timeout below the floor still wins (it is the ceiling)', sttRequestTimeoutMs(6, { capMs: 10_000 }) === 10_000, String(sttRequestTimeoutMs(6, { capMs: 10_000 })));

  check('a bigger body gets proportionally longer (+1 s per 16 KB at the slowest uplink)',
    sttRequestTimeoutMs(20, { capMs: 120_000, bodyBytes: 320 * 1024 }) === 80_000, String(sttRequestTimeoutMs(20, { capMs: 120_000, bodyBytes: 320 * 1024 })));

  let hits = 0;
  let delays: number[] = [];
  const server = createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      const delay = delays[hits] ?? 0;
      hits++;
      setTimeout(() => {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ text: `answer ${hits}`, language: 'en', duration: 1, segments: [] }));
      }, delay);
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const sec = (s: number) => new Float32Array(Math.round(s * 16000)).fill(0.05);
  // Scaled: 100 ms floor, 2× the audio, 5 s ceiling.
  const mk = () => new TranscriptionClient({ serviceUrl: url, requestTimeoutMs: 5000, minRequestTimeoutMs: 100,
    timeoutPerAudioSec: 2, maxRetries: 0, minUploadBytesPerSec: 1e12 });   // isolate audio sizing

  // A short window is given a short timeout: aborted near its own size, not at the 5 s ceiling.
  hits = 0; delays = [800]; logs.length = 0;
  {
    const t0 = Date.now();
    let fault: TranscriptionError | null = null;
    try { await mk().transcribe(sec(0.1)); } catch (e) { fault = e instanceof TranscriptionError ? e : null; }
    const took = Date.now() - t0;
    check('a 0.1 s window times out at its sized timeout (~200 ms), not at the ceiling', fault?.kind === 'timeout' && took < 600, `kind=${fault?.kind} took=${took}ms`);
    const line = logs.find((l) => l.includes('[STT-TIMEOUT]'));
    check('the timeout writes one [STT-TIMEOUT] line', logs.filter((l) => l.includes('[STT-TIMEOUT]')).length === 1, logs.join(' | '));
    check('the line names the request and its audio length and the limit it exceeded',
      !!line && /request \d+/.test(line) && line.includes('0.1s audio') && line.includes('200ms'), String(line));
    check('the line carries no transcript text', !!line && !line.includes('answer'), String(line));
  }
  // A longer window is given longer: the same 800 ms answer is waited for.
  hits = 0; delays = [800]; logs.length = 0;
  {
    const r = await mk().transcribe(sec(1));
    check('a 1 s window (2 s timeout) waits for an 800 ms answer', r.text === 'answer 1' && hits === 1, `text=${r.text} hits=${hits}`);
    check('no timeout line for an answer inside its timeout', !logs.some((l) => l.includes('[STT-TIMEOUT]')), logs.join(' | '));
  }
  await new Promise((r) => setTimeout(r, 900));
  server.close();

  if (failed) { console.log(`\n❌ ${failed} timeout-sizing check(s) failed`); process.exit(1); }
  console.log('✅ timeout-sizing: per-request timeout follows the window; one attributable line per timeout');
}
run().catch((e) => { console.error(e); process.exit(1); });
