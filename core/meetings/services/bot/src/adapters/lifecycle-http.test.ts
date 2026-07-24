/**
 * L3 — lifecycle-http adapter (HTTP callback transport). OFFLINE, NO network.
 *
 * Injects a fake `fetchImpl` that records every call and asserts:
 *   • the POST hits callbackUrl with `content-type: application/json` + `x-internal-secret`,
 *     and the body is the lifecycle.v1 event JSON verbatim (0.11 convention);
 *   • `internalSecret` omitted → no `x-internal-secret` header;
 *   • a transient failure (network throw + non-2xx) is RETRIED with backoff, then succeeds;
 *   • a permanent failure does NOT throw out of `emit` (the bot must not crash).
 * Run: npx tsx src/adapters/lifecycle-http.test.ts
 */
import { createHttpLifecycleSink, type FetchLike } from './lifecycle-http.js';
import type { LifecycleEvent } from '../contracts.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

interface Recorded { url: string; method: string; headers: Record<string, string>; body: string }
const noSleep = async (): Promise<void> => {};

const EVENT: LifecycleEvent = {
  connection_id: 'sess-uid',
  status: 'completed',
  completion_reason: 'stopped',
  exit_code: 0,
};

async function main(): Promise<void> {
  // ── happy: one POST, correct url + headers + verbatim body ──
  {
    const calls: Recorded[] = [];
    const fetchImpl: FetchLike = async (url, init) => { calls.push({ url, ...init }); return { ok: true, status: 200 }; };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://meeting-api:8080/runtime/callback', internalSecret: 'SECRET', fetchImpl, sleep: noSleep });
    await sink.emit(EVENT);
    check('happy: exactly one POST', calls.length === 1, String(calls.length));
    check('happy: hits callbackUrl', calls[0]?.url === 'http://meeting-api:8080/runtime/callback', calls[0]?.url);
    check('happy: method POST', calls[0]?.method === 'POST', calls[0]?.method);
    check('happy: content-type json', calls[0]?.headers['content-type'] === 'application/json', JSON.stringify(calls[0]?.headers));
    check('happy: x-internal-secret header', calls[0]?.headers['x-internal-secret'] === 'SECRET', JSON.stringify(calls[0]?.headers));
    check('happy: body is the lifecycle.v1 event verbatim', calls[0]?.body === JSON.stringify(EVENT), calls[0]?.body);
    check('happy: body round-trips to the event', JSON.stringify(JSON.parse(calls[0]!.body)) === JSON.stringify(EVENT));
  }

  // ── no internalSecret → no x-internal-secret header ──
  {
    const calls: Recorded[] = [];
    const fetchImpl: FetchLike = async (url, init) => { calls.push({ url, ...init }); return { ok: true, status: 204 }; };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, sleep: noSleep });
    await sink.emit(EVENT);
    check('no-secret: x-internal-secret absent', !('x-internal-secret' in (calls[0]?.headers ?? {})), JSON.stringify(calls[0]?.headers));
    check('no-secret: 204 counts as success (single attempt)', calls.length === 1, String(calls.length));
  }

  // ── transient: throw, then 500, then 200 — retried with backoff, succeeds on attempt 3 ──
  {
    const calls: Recorded[] = [];
    const sleeps: number[] = [];
    let n = 0;
    const fetchImpl: FetchLike = async (url, init) => {
      calls.push({ url, ...init });
      n++;
      if (n === 1) throw new Error('ECONNREFUSED');
      if (n === 2) return { ok: false, status: 500 };
      return { ok: true, status: 200 };
    };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, retries: 3, backoffMs: 100, sleep: async (ms) => { sleeps.push(ms); } });
    await sink.emit(EVENT);
    check('retry: three attempts before success', calls.length === 3, String(calls.length));
    check('retry: backoff was bounded + exponential (100, 200)', JSON.stringify(sleeps) === JSON.stringify([100, 200]), JSON.stringify(sleeps));
  }

  // ── permanent failure: every attempt throws → emit MUST NOT throw, stops after `retries` ──
  {
    const calls: Recorded[] = [];
    const fetchImpl: FetchLike = async (url, init) => { calls.push({ url, ...init }); throw new Error('network down'); };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, retries: 3, sleep: noSleep });
    let threw = false;
    try { await sink.emit(EVENT); } catch { threw = true; }
    check('permanent: emit did NOT throw (bot does not crash)', threw === false);
    check('permanent: gave up after exactly `retries` attempts', calls.length === 3, String(calls.length));
  }

  // ── permanent non-2xx: every attempt 503 → also swallowed, bounded attempts ──
  {
    const calls: Recorded[] = [];
    const fetchImpl: FetchLike = async (url, init) => { calls.push({ url, ...init }); return { ok: false, status: 503 }; };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, retries: 2, sleep: noSleep });
    let threw = false;
    try { await sink.emit(EVENT); } catch { threw = true; }
    check('permanent-5xx: emit did NOT throw', threw === false);
    check('permanent-5xx: bounded to `retries` attempts', calls.length === 2, String(calls.length));
  }

  // ── #839 durable acknowledgement: only a 2xx earns "persisted" ──
  {
    let n = 0;
    const fetchImpl: FetchLike = async () => {
      n++;
      return n === 2 ? { ok: true, status: 200 } : { ok: false, status: 503 };
    };
    const sink = createHttpLifecycleSink({
      callbackUrl: 'http://cb', fetchImpl, retries: 2, sleep: noSleep,
    });
    const verdict = await sink.emitDurable!(EVENT);
    check('durable: a later 2xx confirms persisted', verdict === 'persisted', verdict);
    check('durable: confirmation used the bounded retry path', n === 2, String(n));
  }
  {
    const fetchImpl: FetchLike = async () => { throw new Error('network down'); };
    const sink = createHttpLifecycleSink({
      callbackUrl: 'http://cb', fetchImpl, retries: 2, sleep: noSleep,
    });
    const verdict = await sink.emitDurable!(EVENT);
    check('durable: no response remains unconfirmed', verdict === 'unconfirmed', verdict);
  }

  // ── default horizon: 5 attempts × 500ms exponential base (≈7.5s) — a >1s meeting-api blip
  //    must not lose the event (hosted 07-14→07-17: the old ~0.6s horizon dropped callbacks and
  //    the reaper failed seated bots) ──
  {
    const calls: Recorded[] = [];
    const sleeps: number[] = [];
    const fetchImpl: FetchLike = async (url, init) => { calls.push({ url, ...init }); return { ok: false, status: 503 }; };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, sleep: async (ms) => { sleeps.push(ms); } });
    await sink.emit(EVENT);
    check('default horizon: 5 attempts', calls.length === 5, String(calls.length));
    check('default horizon: exponential from 500ms (500,1000,2000,4000)',
      JSON.stringify(sleeps) === JSON.stringify([500, 1000, 2000, 4000]), JSON.stringify(sleeps));
  }

  // ── #530 reachability gate: emitReachable reports channel reachability of the first emit ──
  const JOINING: LifecycleEvent = { connection_id: 'sess-uid', status: 'joining' };

  // healthy 200 → reachable on the FIRST attempt (fast path: near-zero added latency)
  {
    const calls: Recorded[] = [];
    const fetchImpl: FetchLike = async (url, init) => { calls.push({ url, ...init }); return { ok: true, status: 200 }; };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, sleep: noSleep });
    const verdict = await sink.emitReachable!(JOINING);
    check('reach-healthy: verdict=reachable', verdict === 'reachable', verdict);
    check('reach-healthy: single attempt (fast path, no retries)', calls.length === 1, String(calls.length));
    check('reach-healthy: posted the joining event verbatim', calls[0]?.body === JSON.stringify(JOINING), calls[0]?.body);
  }

  // 503 (control plane UP-but-broken) → reachable (either-channel rule guards over-blocking)
  {
    const calls: Recorded[] = [];
    const fetchImpl: FetchLike = async (url, init) => { calls.push({ url, ...init }); return { ok: false, status: 503 }; };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, sleep: noSleep });
    const verdict = await sink.emitReachable!(JOINING);
    check('reach-503: verdict=reachable (host answered → channel is up)', verdict === 'reachable', verdict);
    check('reach-503: returns on the first response (no needless retries)', calls.length === 1, String(calls.length));
  }

  // all-attempts network error (CNI-lag signature) → unreachable, bounded to reachRetries
  {
    const calls: Recorded[] = [];
    const sleeps: number[] = [];
    const fetchImpl: FetchLike = async (url, init) => { calls.push({ url, ...init }); throw new Error('ECONNREFUSED'); };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, reachRetries: 4, reachBackoffMs: 100, sleep: async (ms) => { sleeps.push(ms); } });
    let threw = false;
    let verdict: string | undefined;
    try { verdict = await sink.emitReachable!(JOINING); } catch { threw = true; }
    check('reach-down: did NOT throw (never crashes the bot)', threw === false);
    check('reach-down: verdict=unreachable', verdict === 'unreachable', String(verdict));
    check('reach-down: bounded to reachRetries attempts', calls.length === 4, String(calls.length));
    check('reach-down: bounded exponential backoff (100,200,400)', JSON.stringify(sleeps) === JSON.stringify([100, 200, 400]), JSON.stringify(sleeps));
  }

  // transient: network error then 200 → reachable (rides out a brief programming window)
  {
    let n = 0;
    const fetchImpl: FetchLike = async () => { n++; if (n === 1) throw new Error('EAI_AGAIN'); return { ok: true, status: 200 }; };
    const sink = createHttpLifecycleSink({ callbackUrl: 'http://cb', fetchImpl, reachRetries: 4, reachBackoffMs: 10, sleep: noSleep });
    const verdict = await sink.emitReachable!(JOINING);
    check('reach-transient: verdict=reachable after one retry', verdict === 'reachable' && n === 2, `${verdict}/${n}`);
  }

  if (failed) { console.error(`\n❌ lifecycle-http (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ lifecycle-http (L3): POSTs the lifecycle.v1 event verbatim with x-internal-secret, retries with bounded backoff, never throws out of emit, and emitReachable reports primary-channel reachability for the #530 gate.');
}

void main();
