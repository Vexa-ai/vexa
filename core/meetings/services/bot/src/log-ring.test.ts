/**
 * A bot that FAILED carries out what it was saying (#1189). OFFLINE, no browser/redis/http.
 *
 * The defect this pins: `meetings.data.bot_logs` was fed by the pre-0.12 parent and stops dead at
 * 2026-07-18. The sink survived the rewrite — meeting-api still trims the buffer to 50 KiB
 * oldest-first and still omits it from list responses — but no producer ever filled it, so every
 * join-failure investigation since had zero forensic material.
 *
 * Asserts the ring's contract AND the orchestrator wiring: a failure terminal carries the tail,
 * a clean completion does not, non-terminal events never do, the ring evicts the OLDEST line
 * first at its byte budget, and neither a throwing tap nor an empty ring can perturb the exit.
 * Run: npx tsx src/log-ring.test.ts
 */
import { startBotLogRing, BOT_LOG_TAIL_BYTE_BUDGET } from './log-ring.js';
import { createOrchestrator, CONTROL_PLANE_UNREACHABLE_EXIT } from './orchestrator.js';
import type { Invocation } from './config.js';
import type { LifecycleEvent } from './contracts.js';
import type { JoinDriver, Pipeline, ActsSource, LifecycleSink } from './ports.js';
import { noopAloneness } from './test-doubles.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  process.stdout.write(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}\n`);
  if (!cond) failed++;
};

const inv = {
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'B',
  connectionId: 'conn-logs-1', redisUrl: 'redis://unused:6379', nativeMeetingId: 'abc-defg-hij',
} as Invocation;

/** A console stand-in the ring taps instead of the real one (so the test's own output is clean). */
function fakeConsole() {
  const seen: string[] = [];
  const c: Partial<Console> = {
    log: (...a: unknown[]) => { seen.push(String(a[0])); },
    info: (...a: unknown[]) => { seen.push(String(a[0])); },
    warn: (...a: unknown[]) => { seen.push(String(a[0])); },
    error: (...a: unknown[]) => { seen.push(String(a[0])); },
    debug: (...a: unknown[]) => { seen.push(String(a[0])); },
  };
  return { c, seen };
}

function fakes(events: LifecycleEvent[], outcome: 'admitted' | 'blocked' = 'admitted') {
  const lifecycle: LifecycleSink = { async emit(e) { events.push(e); } };
  const join: JoinDriver = {
    async join(report) { await report('awaiting_admission'); return outcome; },
    onRemoval() { return () => { /* */ }; },
    async leave() { /* */ }, async withdraw() { /* */ },
  };
  const pipeline: Pipeline = { async start() { /* */ }, async stop() { /* */ } };
  const acts: ActsSource = { subscribe() { return () => { /* */ }; } };
  return { lifecycle, join, pipeline, acts, aloneness: noopAloneness() };
}

/** Run to a terminal, ending the active phase with a `leave` act when the bot gets admitted. */
async function runToTerminal(o: ReturnType<typeof createOrchestrator>, admitted: boolean) {
  const p = o.run();
  if (admitted) setTimeout(() => { void o.handle({ action: 'leave' }); }, 5);
  return p;
}

async function main(): Promise<void> {
  // ── 1) the ring: taps every console level, keeps order, never disturbs the original ──
  {
    const { c, seen } = fakeConsole();
    const ring = startBotLogRing({ console: c, now: () => Date.parse('2026-08-17T09:00:00Z') });
    c.log!('[bot] launching browser');
    c.error!('[bot] join driver: admission timed out');
    c.warn!('[bot] withdrew');
    ring.stop();
    c.log!('after stop — must not be captured');

    const t = ring.tail();
    check('every console level is taped', t.length === 3, JSON.stringify(t));
    check('oldest → newest order', /launching browser/.test(t[0]) && /withdrew/.test(t[2]), JSON.stringify(t));
    check('the level is stamped', /\sERROR\s/.test(t[1]), t[1]);
    check('the producer clock is stamped', t[0].startsWith('2026-08-17T09:00:00.000Z'), t[0]);
    check('the ORIGINAL console still ran (a tap must be invisible)', seen.length === 4, String(seen.length));
    check('stop() detaches', !ring.tail().some((l) => /after stop/.test(l)));
    check('nothing logged ⇒ empty tail', startBotLogRing({ console: fakeConsole().c }).tail().length === 0);
  }

  // ── 2) the byte budget: OLDEST evicted first, newest always survives ──
  {
    const { c } = fakeConsole();
    const ring = startBotLogRing({ console: c, maxBytes: 4 * 1024 });
    for (let i = 0; i < 200; i++) c.log!(`L${String(i).padStart(4, '0')}:` + 'x'.repeat(100));
    const t = ring.tail();
    ring.stop();
    const bytes = t.reduce((n, l) => n + Buffer.byteLength(l, 'utf8') + 1, 0);
    check('the ring stays inside its byte budget', bytes <= 4 * 1024, `${bytes} bytes`);
    check('it did evict (the run overflowed)', t.length < 200 && ring.truncated(), `${t.length} lines`);
    check('the NEWEST line survives', /L0199:/.test(t[t.length - 1]), t[t.length - 1]);
    check('the OLDEST line is gone', !t.some((l) => /L0000:/.test(l)));
    check('the default budget mirrors meeting-api', BOT_LOG_TAIL_BYTE_BUDGET === 50 * 1024);
    // One line larger than the whole budget still says SOMETHING rather than nothing.
    const { c: c2 } = fakeConsole();
    const tiny = startBotLogRing({ console: c2, maxBytes: 16 });
    c2.log!('a line far longer than sixteen bytes');
    check('an over-budget single line is kept, not dropped to silence', tiny.tail().length === 1);
    tiny.stop();
  }

  // ── 3) the wiring: a FAILED terminal carries bot_logs; a clean completion does NOT ──
  {
    const events: LifecycleEvent[] = [];
    const o = createOrchestrator(inv, { ...fakes(events, 'blocked'), logTail: () => ['[bot] lobby: never admitted'] });
    const r = await runToTerminal(o, false);
    const terminal = events[events.length - 1];
    check('the run failed (not admitted)', r.status === 'failed', JSON.stringify(r));
    check('the FAILED terminal carries bot_logs', Array.isArray(terminal.bot_logs) && terminal.bot_logs!.length === 1,
      JSON.stringify(terminal.bot_logs));
    check('the tail is the bot\'s own line', /never admitted/.test(terminal.bot_logs![0]));
    check('NO non-terminal event carries bot_logs',
      events.slice(0, -1).every((e) => e.bot_logs === undefined),
      JSON.stringify(events.map((e) => [e.status, !!e.bot_logs])));
  }
  {
    const events: LifecycleEvent[] = [];
    const o = createOrchestrator(inv, { ...fakes(events), logTail: () => ['[bot] a perfectly healthy meeting'] });
    const r = await runToTerminal(o, true);
    const terminal = events[events.length - 1];
    check('a clean run completes', r.status === 'completed', JSON.stringify(r));
    check('a CLEAN COMPLETION carries no bot_logs', terminal.bot_logs === undefined, JSON.stringify(terminal.bot_logs));
    check('and no event in the clean run does', events.every((e) => e.bot_logs === undefined));
  }

  // ── 4) never at the cost of the report: a throwing / empty tap cannot change the exit ──
  {
    const events: LifecycleEvent[] = [];
    const o = createOrchestrator(inv, { ...fakes(events, 'blocked'), logTail: () => { throw new Error('tap exploded'); } });
    const r = await runToTerminal(o, false);
    check('a THROWING log tap still yields the terminal', events[events.length - 1].status === 'failed');
    check('…with the same exit code', r.exitCode === 1, String(r.exitCode));
    check('…and simply without the tail', events[events.length - 1].bot_logs === undefined);
  }
  {
    const events: LifecycleEvent[] = [];
    const o = createOrchestrator(inv, { ...fakes(events, 'blocked'), logTail: () => [] });
    await runToTerminal(o, false);
    check('an EMPTY tail omits the key entirely (never bot_logs: [])',
      !('bot_logs' in events[events.length - 1]));
  }
  {
    // No logTail supplied at all (self-host / test composition) — the field simply never appears.
    const events: LifecycleEvent[] = [];
    const o = createOrchestrator(inv, fakes(events, 'blocked'));
    await runToTerminal(o, false);
    check('no logTail port ⇒ no bot_logs, no crash', events[events.length - 1].bot_logs === undefined);
  }

  // ── 5) the control-plane-unreachable refusal is a failure too — it carries the tail ──
  {
    const events: LifecycleEvent[] = [];
    const base = fakes(events, 'blocked');
    const o = createOrchestrator(inv, {
      ...base,
      lifecycle: { ...base.lifecycle, async emitReachable(e) { events.push(e); return 'unreachable'; } },
      reachability: { async probeSecondary() { return false; } },
      logTail: () => ['[bot] callback POST failed: ECONNREFUSED'],
    });
    const r = await o.run();
    check('both channels down ⇒ refuse to join', r.exitCode === CONTROL_PLANE_UNREACHABLE_EXIT, String(r.exitCode));
    check('the refusal terminal ALSO carries the tail (why it could not report)',
      /ECONNREFUSED/.test(String(events[events.length - 1].bot_logs?.[0])),
      JSON.stringify(events[events.length - 1].bot_logs));
  }

  if (failed > 0) { process.stdout.write(`\n❌ log-ring: ${failed} check(s) failed\n`); process.exit(1); }
  process.stdout.write('\n✅ log-ring: a bot that FAILED carries out what it was saying — ≤50 KiB, oldest evicted first, never on a clean completion, and never at the cost of the terminal event itself.\n');
}

void main();
