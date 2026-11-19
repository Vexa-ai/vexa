/**
 * An overloaded STT is backed off from, not hammered (incident 2026-10-14).
 *
 * The shared STT gateway answered a few 429s and slow reads. Every request retried on its own fixed
 * 1 s / 2 s / 4 s schedule, ignoring Retry-After, while other requests kept going out: the bot's rate
 * went from 37 to 120 requests a minute, the gateway's slots filled and it refused 98 requests in two
 * minutes. Here a local server stands in for the STT and the delays are scaled down.
 *
 *   npx tsx src/overload.test.ts
 */
import { createServer, type ServerResponse } from 'node:http';
import { TranscriptionClient, TranscriptionError, SttGate } from './index.js';
import { setLogger } from './log.js';

setLogger(() => {});
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Handler = (n: number, res: ServerResponse) => void;
let handler: Handler = () => {};
let hits = 0, active = 0, maxActive = 0;
const sentAt: number[] = [];
const ok = (res: ServerResponse, text: string) => {
  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify({ text, language: 'en', duration: 1, segments: [] }));
};

async function run() {
  const server = createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      const n = ++hits;
      sentAt.push(Date.now());
      active++; maxActive = Math.max(maxActive, active);
      res.on('close', () => { active--; });
      handler(n, res);
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const pcm = new Float32Array(1600).fill(0.05);
  const reset = (h: Handler) => { handler = h; hits = 0; active = 0; maxActive = 0; sentAt.length = 0; };

  // 1. A 429 carrying Retry-After is waited out before the next request, whatever the retry delay.
  reset((n, res) => {
    if (n === 1) { res.writeHead(429, { 'retry-after': '1' }); res.end('slow down'); return; }
    ok(res, 'after wait');
  });
  {
    const c = new TranscriptionClient({ serviceUrl: url, maxRetries: 3, retryDelayMs: 1 });
    const r = await c.transcribe(pcm);
    const gap = sentAt[1] - sentAt[0];
    check('a 429 with Retry-After: 1 is honoured (next request >= ~1 s later)', r.text === 'after wait' && gap >= 950, `gap=${gap}ms text=${r.text}`);
  }

  // 2. A 500 whose body says the upstream answered 429 is overload too: backed off, typed as overload.
  reset((n, res) => {
    if (n === 1) { res.writeHead(500); res.end('{"error":"upstream returned 429: Too Many Requests"}'); return; }
    ok(res, 'recovered');
  });
  {
    const c = new TranscriptionClient({ serviceUrl: url, maxRetries: 3, retryDelayMs: 1, overloadBackoffBaseMs: 400, overloadBackoffMaxMs: 400 });
    const r = await c.transcribe(pcm);
    const gap = sentAt[1] - sentAt[0];
    check('a 500 relaying an upstream 429 is backed off (jittered >= half the base)', r.text === 'recovered' && gap >= 190, `gap=${gap}ms`);
  }
  reset((_n, res) => { res.writeHead(503); res.end('fallback saturated'); });
  {
    const c = new TranscriptionClient({ serviceUrl: url, maxRetries: 0, retryDelayMs: 1, overloadBackoffBaseMs: 1 });
    let f: TranscriptionError | null = null;
    try { await c.transcribe(pcm); } catch (e) { f = e as TranscriptionError; }
    check('a 503 "saturated" surfaces as a retryable overload fault', !!f && f.overloaded === true && f.retryable, JSON.stringify(f && { k: f.kind, o: f.overloaded }));
  }

  // 3. One shared breaker per bot: 12 windows at once against an STT that is overloaded for 1.5 s.
  //    In flight stays under the cap, the overload is not multiplied by retries, nothing is lost.
  {
    const overloadedUntil = Date.now() + 1500;
    reset((_n, res) => {
      if (Date.now() < overloadedUntil) { res.writeHead(429); res.end('busy'); return; }
      setTimeout(() => ok(res, 'done'), 50);
    });
    const gate = new SttGate({ maxConcurrent: 4, breakerThreshold: 3, backoffBaseMs: 200, backoffMaxMs: 400 });
    const c = new TranscriptionClient({ serviceUrl: url, maxRetries: 50, retryDelayMs: 1, gate });
    const calls = Array.from({ length: 12 }, () => c.transcribe(pcm).then((r) => r.text, (e) => `ERR ${e?.message}`));
    await sleep(500);
    check('the gate reports back-pressure while the STT is overloaded', gate.backingOff(), 'backingOff()=false');
    const texts = await Promise.all(calls);
    const duringOverload = sentAt.filter((t) => t < overloadedUntil).length;
    check('never more than maxConcurrent requests in flight', maxActive <= 4, `maxActive=${maxActive}`);
    console.log("    sent during overload:", duringOverload); check("requests during the overload stay bounded (no per-request retry storm)", duringOverload <= 10, `sent during overload=${duringOverload}`);
    check('every window is transcribed once the STT recovers (none lost)', texts.every((t) => t === 'done'), JSON.stringify(texts));
    check('the gate is clear after recovery', !gate.backingOff());
    check('the gate records its peak in flight', gate.stats().maxInFlight <= 4, JSON.stringify(gate.stats()));
  }

  server.close();
  if (failed) { console.error(`\n❌ stt overload: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ stt overload: Retry-After honoured, jittered backoff, one shared breaker, bounded in-flight, nothing lost');
}
run().catch((e) => { console.error(e); process.exit(1); });
