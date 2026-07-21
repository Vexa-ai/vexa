/**
 * L3 — makeLazyConnect (the bot's redis first-use connect memo). OFFLINE, NO real redis.
 *
 * The defect this guards: a boolean flipped only AFTER `connect()` resolves is a check-then-act
 * race — two concurrent first-use callers both see it false and both call connect(), and node-redis
 * v4 throws "Socket already opened" on the second. The negative control below reproduces exactly
 * that (RED); makeLazyConnect connects once under the same concurrency (GREEN).
 *
 * Run: npx tsx src/adapters/redis-lazy-connect.test.ts
 */
import { makeLazyConnect, type LazyConnectable } from './redis-lazy-connect.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

/** A fake node-redis client whose connect() only resolves when we release the deferred — so the
 *  window between "first caller awaits connect" and "connect resolves" is under test control, which
 *  is precisely where the race lives. Records every connect()/quit() call. */
function fakeClient(opts: { failFirstConnect?: boolean } = {}) {
  let connectCalls = 0;
  let quitCalls = 0;
  let open = false;
  const gates: Array<() => void> = [];
  const client: LazyConnectable = {
    connect() {
      connectCalls++;
      const nth = connectCalls;
      return new Promise<void>((resolve, reject) => {
        gates.push(() => {
          if (opts.failFirstConnect && nth === 1) { reject(new Error('boom')); return; }
          open = true;
          resolve();
        });
      });
    },
    quit() { quitCalls++; open = false; return Promise.resolve(); },
    get isOpen() { return open; },
  };
  return {
    client,
    releaseAll: () => { const g = gates.splice(0); g.forEach((fn) => fn()); },
    get connectCalls() { return connectCalls; },
    get quitCalls() { return quitCalls; },
    get isOpen() { return open; },
  };
}

/** The OLD, buggy memo (flag flipped after await) — the negative control. */
function buggyEnsureOver(client: LazyConnectable) {
  let connected = false;
  return async (): Promise<void> => {
    if (!connected) {
      await client.connect();
      connected = true;
    }
  };
}

async function main(): Promise<void> {
  // ── A2 (negative control first): the OLD memo double-connects under concurrent first use ──
  {
    const fake = fakeClient();
    const ensure = buggyEnsureOver(fake.client);
    const a = ensure();
    const b = ensure(); // second caller races before the first connect resolved
    fake.releaseAll();
    await Promise.all([a, b]);
    check('control: old flag-after-await memo calls connect() TWICE (the bug this test discriminates)',
      fake.connectCalls === 2, `connectCalls=${fake.connectCalls}`);
  }

  // ── A1: makeLazyConnect connects EXACTLY ONCE under the same concurrency ──
  {
    const fake = fakeClient();
    const lazy = makeLazyConnect(fake.client);
    const calls = [lazy.ensure(), lazy.ensure(), lazy.ensure()]; // 3 concurrent first-use callers
    fake.releaseAll();
    await Promise.all(calls);
    check('lazy: concurrent first-use callers share ONE connect()', fake.connectCalls === 1, `connectCalls=${fake.connectCalls}`);
    // and a later ensure() after connected does not reconnect
    await lazy.ensure();
    check('lazy: ensure() after connected does not reconnect', fake.connectCalls === 1, `connectCalls=${fake.connectCalls}`);
  }

  // ── A3: a failed connect is not sticky — the memo clears so a later ensure() retries ──
  {
    const fake = fakeClient({ failFirstConnect: true });
    const lazy = makeLazyConnect(fake.client);
    const first = lazy.ensure().then(() => 'ok', () => 'rejected');
    fake.releaseAll();
    check('lazy: failed connect rejects the caller', (await first) === 'rejected');
    const second = lazy.ensure();
    fake.releaseAll();
    await second;
    check('lazy: a later ensure() retries after a failed connect', fake.connectCalls === 2, `connectCalls=${fake.connectCalls}`);
  }

  // ── A4: quit settles an in-flight connect and only quit()s an open socket ──
  {
    const fake = fakeClient();
    const lazy = makeLazyConnect(fake.client);
    const pendingEnsure = lazy.ensure();
    const pendingQuit = lazy.quit();   // called while connect is still in flight
    fake.releaseAll();
    await Promise.all([pendingEnsure, pendingQuit]);
    check('lazy: quit() waits out the in-flight connect then quit()s the open socket', fake.quitCalls === 1, `quitCalls=${fake.quitCalls}`);

    // quit when never connected → no quit() call (isOpen was false)
    const fake2 = fakeClient();
    const lazy2 = makeLazyConnect(fake2.client);
    await lazy2.quit();
    check('lazy: quit() with no connection does not call client.quit()', fake2.quitCalls === 0, `quitCalls=${fake2.quitCalls}`);
  }

  // ── quit() must be BOUNDED: node-redis v4's DEFAULT reconnectStrategy retries forever, so
  //    connect() against an unreachable server stays PENDING — never resolves, never rejects.
  //    An unbounded `await connecting` there hangs teardown: a bot whose redis is down never
  //    exits. The control below is the pending-forever client; without the bound this HANGS.
  {
    const neverSettles: LazyConnectable = {
      connect() { return new Promise<void>(() => { /* pending forever, by construction */ }); },
      quit() { return Promise.resolve(); },
      get isOpen() { return false; },
    } as unknown as LazyConnectable;

    const lazy = makeLazyConnect(neverSettles, { quitSettleMs: 20 });
    void lazy.ensure().catch(() => undefined);
    const raced = await Promise.race([
      lazy.quit().then(() => 'returned'),
      new Promise((r) => setTimeout(() => r('HUNG'), 1000)),
    ]);
    check('lazy: quit() does not hang on a connect that never settles (unreachable redis)',
      raced === 'returned', String(raced));
  }

  if (failed) { console.error(`\n❌ redis-lazy-connect (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ redis-lazy-connect (L3): concurrent first-use callers share one connect (no "Socket already opened" race); failed connect retries; quit settles in-flight, is bounded against a pending-forever connect, and guards isOpen.');
}

void main();
