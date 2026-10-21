/**
 * Standalone test for gotoWithRetry — the first navigation every platform's join makes.
 * A fake page whose goto throws a scripted sequence stands in for Playwright; sleep is
 * injected so the backoff costs nothing.
 *
 * Run: npx tsx src/shared/navigate.test.ts
 */

import { gotoWithRetry } from './navigate';

let passed = 0;
let failed = 0;

function expect(name: string, actual: any, expected: any) {
  if (actual === expected) {
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
  } else {
    console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`);
    console.log(`        expected: ${JSON.stringify(expected)}`);
    console.log(`        actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

const URL_ = 'https://meet.google.com/abc-defg-hij';
const CLOSED = `page.goto: net::ERR_CONNECTION_CLOSED at ${URL_}`;
const TARGET_CLOSED = 'page.goto: Target page, context or browser has been closed';
const NON_NETWORK = 'page.goto: Protocol error (Page.navigate): Cannot navigate to invalid URL';

/** A page whose goto fails with each scripted message in turn, then succeeds. */
function scriptedPage(script: string[]) {
  const calls: Array<{ url: string; options: any }> = [];
  const page = {
    calls,
    async goto(url: string, options: any) {
      calls.push({ url, options });
      const next = script[calls.length - 1];
      if (next !== undefined) throw new Error(next);
      return null;
    },
  };
  return page;
}

function harness() {
  const sleeps: number[] = [];
  const logs: string[] = [];
  return {
    sleeps,
    logs,
    opts: {
      sleep: async (ms: number) => { sleeps.push(ms); },
      log: (m: string) => { logs.push(m); },
    },
  };
}

async function settle(p: Promise<unknown>): Promise<{ ok: boolean; error?: Error }> {
  try { await p; return { ok: true }; } catch (e: any) { return { ok: false, error: e }; }
}

async function main() {
  console.log('\ngotoWithRetry');

  {
    const page = scriptedPage([CLOSED, CLOSED]);
    const h = harness();
    const r = await settle(gotoWithRetry(page as any, URL_, { waitUntil: 'domcontentloaded', ...h.opts }));
    expect('CLOSED, CLOSED, ok → resolves', r.ok, true);
    expect('CLOSED, CLOSED, ok → 3 goto calls', page.calls.length, 3);
    expect('backoff schedule is 1000 then 3000', h.sleeps.join(','), '1000,3000');
    expect('each failed attempt is logged with its net::ERR code',
      h.logs.filter((l) => l.includes('net::ERR_CONNECTION_CLOSED')).length, 2);
    expect('waitUntil is passed through', page.calls[0].options.waitUntil, 'domcontentloaded');
    expect('timeout defaults to 60000', page.calls[0].options.timeout, 60000);
  }

  {
    const page = scriptedPage([CLOSED, CLOSED, CLOSED]);
    const h = harness();
    const r = await settle(gotoWithRetry(page as any, URL_, { waitUntil: 'domcontentloaded', ...h.opts }));
    const msg = r.error?.message ?? '';
    expect('CLOSED×3 → rejects', r.ok, false);
    expect('CLOSED×3 → 3 goto calls', page.calls.length, 3);
    expect('exhaustion names the attempt count', msg.startsWith('page.goto failed after 3 attempts: '), true);
    expect('exhaustion keeps the original text', msg.includes(CLOSED), true);
    // The bot's join-evidence classifier (services/bot, outside this package's isolation
    // boundary) reads a NAVIGATION failure off the `net::err` marker, lowercased. Its own test
    // carries this exact message as a row; here we pin the marker survives the rethrow.
    expect('exhaustion carries the navigation marker net::err', msg.toLowerCase().includes('net::err'), true);
  }

  {
    const page = scriptedPage([TARGET_CLOSED]);
    const h = harness();
    const r = await settle(gotoWithRetry(page as any, URL_, { waitUntil: 'load', ...h.opts }));
    expect('target closed → rejects', r.ok, false);
    expect('target closed → 1 goto call (never retried)', page.calls.length, 1);
    expect('target closed → original error rethrown unchanged', r.error?.message, TARGET_CLOSED);
    expect('target closed → no backoff', h.sleeps.length, 0);
  }

  {
    const page = scriptedPage([NON_NETWORK]);
    const h = harness();
    const r = await settle(gotoWithRetry(page as any, URL_, { waitUntil: 'domcontentloaded', ...h.opts }));
    expect('non-network error → rejects', r.ok, false);
    expect('non-network error → 1 goto call (never retried)', page.calls.length, 1);
    expect('non-network error → original error rethrown unchanged', r.error?.message, NON_NETWORK);
  }

  {
    const page = scriptedPage([`page.goto: net::ERR_SOCKET_NOT_CONNECTED at ${URL_}`]);
    const h = harness();
    const r = await settle(gotoWithRetry(page as any, URL_, { waitUntil: 'domcontentloaded', timeoutMs: 30000, ...h.opts }));
    expect('SOCKET_NOT_CONNECTED (#1739), ok → resolves', r.ok, true);
    expect('SOCKET_NOT_CONNECTED, ok → 2 goto calls', page.calls.length, 2);
    expect('explicit timeoutMs is passed through', page.calls[0].options.timeout, 30000);
  }

  console.log(`\n${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
}

main().catch((e) => { console.error(e); process.exit(1); });
