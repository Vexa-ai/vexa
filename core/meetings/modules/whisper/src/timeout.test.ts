/**
 * The per-request STT timeout matches a remote STT's measured latency, and a retry after a timeout
 * resends the same window once — one result, never two transcripts of the same audio.
 *
 * Against transcription.vexa.ai (2026-10-11) a 6 s window took 15–28 s at the tail; the 30 s default
 * aborted live requests ("stt timeout: This operation was aborted") and started them again.
 *
 * A local HTTP server stands in for the STT; delays are scaled down so the test runs in ~1 s.
 *   npx tsx src/timeout.test.ts
 */
import { createServer } from 'node:http';
import { TranscriptionClient, TranscriptionError, DEFAULT_STT_REQUEST_TIMEOUT_MS } from './index.js';
import { setLogger } from './log.js';

setLogger(() => {});
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

async function run() {
  check('the default per-request timeout is sized for a remote realtime STT (>= 20 s)',
    DEFAULT_STT_REQUEST_TIMEOUT_MS >= 20_000, String(DEFAULT_STT_REQUEST_TIMEOUT_MS));
  const dflt = new TranscriptionClient({ serviceUrl: 'http://stt.test' }) as unknown as { requestTimeoutMs: number };
  check('a client built without a timeout uses that default', dflt.requestTimeoutMs === DEFAULT_STT_REQUEST_TIMEOUT_MS, String(dflt.requestTimeoutMs));

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
  const port = (server.address() as { port: number }).port;
  const pcm = new Float32Array(1600).fill(0.05);
  const url = `http://127.0.0.1:${port}`;

  // A slow answer inside the configured timeout is waited for: one request, its text.
  hits = 0; delays = [300];
  {
    const c = new TranscriptionClient({ serviceUrl: url, requestTimeoutMs: 1000, maxRetries: 3, retryDelayMs: 1 });
    const r = await c.transcribe(pcm);
    check('a slow answer within the configured timeout is waited for (no abort, no retry)', hits === 1 && r.text === 'answer 1', `hits=${hits} text=${r.text}`);
  }
  // The configured timeout is respected: past it the request is aborted as a typed timeout.
  hits = 0; delays = [600];
  {
    const c = new TranscriptionClient({ serviceUrl: url, requestTimeoutMs: 150, maxRetries: 0 });
    const t0 = Date.now();
    let fault: TranscriptionError | null = null;
    try { await c.transcribe(pcm); } catch (e) { fault = e instanceof TranscriptionError ? e : null; }
    const took = Date.now() - t0;
    check('the configured timeout aborts a request that outlives it, as kind=timeout', fault?.kind === 'timeout', String(fault?.message));
    check('the abort happens at the configured timeout, not the default', took >= 140 && took < 500, `took=${took}ms`);
  }
  // A retry after a timeout resends the same window once; the caller receives exactly one result.
  hits = 0; delays = [600, 10];
  {
    const c = new TranscriptionClient({ serviceUrl: url, requestTimeoutMs: 200, maxRetries: 3, retryDelayMs: 1 });
    const r = await c.transcribe(pcm);
    check('a timed-out attempt is retried and ONE result comes back (the window is not transcribed twice)',
      hits === 2 && r.text === 'answer 2', `hits=${hits} text=${r.text}`);
  }
  await new Promise((r) => setTimeout(r, 700));   // let the abandoned first answer drain
  server.close();

  if (failed) { console.log(`\n❌ ${failed} timeout check(s) failed`); process.exit(1); }
  console.log('✅ timeout: realtime-sized default, configured timeout respected, retry yields one result');
}
run().catch((e) => { console.error(e); process.exit(1); });
