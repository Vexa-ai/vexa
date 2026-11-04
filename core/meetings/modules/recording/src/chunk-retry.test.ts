/**
 * chunk-retry — RecordingService.uploadChunk must survive a meeting-api restart.
 *
 * Incident (claude-rc meeting 277, 2026-11-02): meeting-api was recreated mid-call. The bot's
 * chunk 114 failed with `getaddrinfo EAI_AGAIN meeting-api`, chunk 115 with `ECONNREFUSED`;
 * uploadChunk gave each one up after 3 attempts in 1.5 s, and the master got a 30 s hole where
 * people were talking. These arms run the real HTTP wire against a local server:
 *   1. the server is DOWN for 2.5 s, then comes up → the chunk lands (red before: lost at 1.5 s);
 *   2. 503 four times, then 200 → the chunk lands (red before: lost after the 3rd 503);
 *   3. a 4xx is permanent → exactly minAttempts requests, then it throws (unchanged behaviour);
 *   4. the server never comes up → it throws once the time budget is spent (bounded, not forever);
 *   5. the classifier: no HTTP answer, 5xx, 408, 429 are transient; other 4xx are not.
 *
 * No assert lib — same shape as the other *.test.ts here (tsx + exit code).
 */
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { RecordingService, isTransientUploadError } from './recording';
import { setLoggers } from './log';

setLoggers({ log: () => undefined, logJSON: () => undefined });

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** Short delays so the arms run in seconds; the budget logic is the same as the defaults. */
const FAST = { baseDelayMs: 100, maxDelayMs: 200, budgetMs: 10_000 };

interface Receiver { url: string; port: number; seqs: number[]; requests: () => number; close: () => Promise<void> }

/** A meeting-api stand-in: answers each upload with `status(n)` (n = 1-based request count). */
function receiver(status: (n: number) => number, port = 0): Promise<Receiver> {
  let n = 0;
  const seqs: number[] = [];
  const server = http.createServer((req, res) => {
    const parts: Buffer[] = [];
    req.on('data', (d: Buffer) => parts.push(d));
    req.on('end', () => {
      n += 1;
      const code = status(n);
      if (code >= 200 && code < 300) {
        const m = Buffer.concat(parts).toString('latin1').match(/name="chunk_seq"\r\n\r\n(\d+)\r\n/);
        if (m) seqs.push(Number(m[1]));
      }
      res.writeHead(code, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: code }));
    });
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => {
    const p = (server.address() as AddressInfo).port;
    resolve({
      url: `http://127.0.0.1:${p}/internal/recordings/upload`, port: p, seqs,
      requests: () => n,
      close: () => new Promise<void>((r) => server.close(() => r())),
    });
  }));
}

/** A port nothing listens on yet (bind, read the port, release it). */
async function freePort(): Promise<number> {
  const r = await receiver(() => 200);
  await r.close();
  return r.port;
}

async function main(): Promise<void> {
  const chunk = Buffer.alloc(2048, 7);

  // ── 1) meeting-api down for 2.5 s (ECONNREFUSED), then back → the chunk still lands ─────────
  {
    const port = await freePort();
    const svc = new RecordingService(277, 'sess-restart', 16000, 1, { chunkRetry: FAST });
    let up: Receiver | undefined;
    const comesBack = sleep(2500).then(async () => { up = await receiver(() => 200, port); });
    const t0 = Date.now();
    const result = await svc
      .uploadChunk(`http://127.0.0.1:${port}/internal/recordings/upload`, 's', chunk, 114, false, 'webm')
      .then(() => 'stored', (e: Error) => `lost: ${e.message}`);
    await comesBack;
    check('restart: a chunk produced while meeting-api is down still lands once it is back',
      result === 'stored', `${result} after ${Date.now() - t0} ms`);
    check('restart: the receiver got chunk_seq 114', (up?.seqs ?? []).join(',') === '114',
      JSON.stringify(up?.seqs));
    await up?.close();
  }

  // ── 2) 503 four times, then 200 → the chunk lands ────────────────────────────────────────────
  {
    const r = await receiver((n) => (n <= 4 ? 503 : 200));
    const svc = new RecordingService(277, 'sess-503', 16000, 1, { chunkRetry: FAST });
    const result = await svc.uploadChunk(r.url, 's', chunk, 115, false, 'webm')
      .then(() => 'stored', (e: Error) => `lost: ${e.message}`);
    check('5xx: four 503s then 200 → stored', result === 'stored', result);
    check('5xx: exactly 5 requests', r.requests() === 5, String(r.requests()));
    await r.close();
  }

  // ── 3) a 4xx is permanent → minAttempts (3) requests, then it throws ─────────────────────────
  {
    const r = await receiver(() => 404);
    const svc = new RecordingService(277, 'sess-404', 16000, 1, { chunkRetry: FAST });
    const result = await svc.uploadChunk(r.url, 's', chunk, 3, false, 'webm')
      .then(() => 'stored', (e: Error & { httpStatus?: number }) => `lost:${e.httpStatus}`);
    check('4xx: not retried past minAttempts (3 requests)', r.requests() === 3, String(r.requests()));
    check('4xx: rejects with httpStatus 404', result === 'lost:404', result);
    await r.close();
  }

  // ── 4) meeting-api never comes back → gives up once the budget is spent ──────────────────────
  {
    const port = await freePort();
    const svc = new RecordingService(277, 'sess-dead', 16000, 1,
      { chunkRetry: { ...FAST, budgetMs: 800 } });
    const t0 = Date.now();
    const result = await svc
      .uploadChunk(`http://127.0.0.1:${port}/internal/recordings/upload`, 's', chunk, 9, false, 'webm')
      .then(() => 'stored', () => 'lost');
    const took = Date.now() - t0;
    check('budget: still down after the budget → the chunk is given up (not retried forever)',
      result === 'lost', result);
    check('budget: gave up within the 800 ms budget (+ slack)', took < 2000, `${took} ms`);
  }

  // ── 5) the transient/permanent classifier ────────────────────────────────────────────────────
  {
    const transient = (e: unknown): boolean => isTransientUploadError(e);
    check('classify: EAI_AGAIN (DNS while the container is recreated) is transient',
      transient(Object.assign(new Error('getaddrinfo EAI_AGAIN meeting-api'), { code: 'EAI_AGAIN' })));
    check('classify: ECONNREFUSED is transient',
      transient(Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' })));
    check('classify: our own request timeout is transient', transient(new Error('Upload timed out after 30000ms')));
    check('classify: 500 / 502 / 503 / 408 / 429 are transient',
      [500, 502, 503, 408, 429].every((s) => transient({ httpStatus: s })));
    check('classify: 400 / 401 / 403 / 404 / 413 / 422 are permanent',
      [400, 401, 403, 404, 413, 422].every((s) => !transient({ httpStatus: s })));
  }

  if (failed) { console.error(`\n❌ chunk-retry: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ chunk-retry: a chunk survives a meeting-api restart (refused → back, 5xx → 200); a 4xx stays permanent; a dead receiver is given up after the time budget.');
}

void main();
