/**
 * STRESS / SHAKE — the bot orchestrator under floods + failure-under-load (L2, offline).
 *
 * Proves the worker stays well-behaved when hammered: a flood of `leave` acts yields EXACTLY ONE clean
 * terminal (no double-terminal, no hang); a flood of rapid status reports serialises through the
 * report-chain and still reaches `active`; and a pipeline that fails under load LEAVES the meeting (no
 * ghost participant) and emits one `failed`. Every emitted event stays legal lifecycle.v1.
 *
 * Run: npx tsx src/stress.test.ts
 */
import { createOrchestrator } from './orchestrator.js';
import { canTransition, type Act, type BotStatus, type LifecycleEvent } from './contracts.js';
import type { JoinDriver, JoinOutcome, Pipeline, LifecycleSink, ActsSource } from './ports.js';
import type { Invocation } from './config.js';
import { noopAloneness, noopPipeline, noopActs } from './test-doubles.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '\x1b[32m✅\x1b[0m' : '\x1b[31m❌\x1b[0m'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const inv = (): Invocation => ({
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'B',
  redisUrl: 'redis://r:6379', connectionId: 'sess-uid', container_name: 'mtg-abc123-bot',
  nativeMeetingId: 'abc-defg-hij',
});

const sink = (): LifecycleSink & { readonly events: LifecycleEvent[] } => {
  const events: LifecycleEvent[] = [];
  return { events, async emit(e: LifecycleEvent) { events.push(e); } };
};
// The orchestrator subscribes to acts DURING the lobby now (before admission, #889), so acts can
// arrive in ANY phase. This double CAPTURES the handler and lets the driver fire a flood at a chosen
// phase (from inside join(), after it has reported the phase it wants to stress).
const capturingActs = () => {
  let handler: (a: Act) => void = () => { /* */ };
  const acts: ActsSource = { subscribe(h) { handler = (a) => void h(a); return () => { /* */ }; } };
  return { acts, flood: (n: number) => { for (let i = 0; i < n; i++) handler({ action: 'leave' }); } };
};
const noActs = noopActs();
const noAloneness = noopAloneness();

const terminals = (e: LifecycleEvent[]) => e.filter((x) => x.status === 'completed' || x.status === 'failed');
const allLegal = (e: LifecycleEvent[]) =>
  e.map((x) => x.status).every((st, i, s) => i === 0 || st === s[i - 1] || canTransition(s[i - 1], st));

async function main(): Promise<void> {
  console.log('\n=== bot orchestrator stress (shake) ===');

  // ── (1) ACTIVE-phase act flood: 200 concurrent `leave` acts after admission → ONE clean completed ──
  {
    const lc = sink();
    const { acts, flood } = capturingActs();
    const join: JoinDriver = {
      async join(report) { await report('awaiting_admission'); await report('active'); flood(200); return 'admitted' as JoinOutcome; },
      onRemoval() { return () => { /* */ }; },
      async leave() { /* */ },
      async withdraw() { /* */ },
    };
    const o = createOrchestrator(inv(), { lifecycle: lc, join, pipeline: noopPipeline(), acts, aloneness: noAloneness });
    const res = await o.run();  // 200 leave acts flood in during the active phase
    check('act-flood: completed(stopped)', res.status === 'completed' && res.completionReason === 'stopped');
    check('act-flood: EXACTLY one terminal (no double-terminal under 200 leaves)', terminals(lc.events).length === 1,
      `got ${terminals(lc.events).length}`);
    check('act-flood: every event legal', allLegal(lc.events));
  }

  // ── (1b) LOBBY-phase act flood (#889): 200 `leave` acts while still knocking → ONE clean WITHDRAW ──
  // The lobby `leave` (meeting-api's stop command) now aborts the join under load: the first leave
  // withdraws (once — the abort resolver is one-shot), the other 199 are no-ops, and the bot terminates
  // failed(awaiting_admission / stopped) with EXACTLY one terminal. No double-withdraw, no hang.
  {
    const lc = sink();
    const { acts, flood } = capturingActs();
    let withdrew = 0;
    const lobbyJoin: JoinDriver = {
      // Reach the lobby, flood, then block (the real lobby never resolves on its own) — the flood aborts it.
      async join(report) { await report('awaiting_admission'); flood(200); return new Promise<JoinOutcome>(() => { /* never */ }); },
      onRemoval() { return () => { /* */ }; },
      async leave() { /* */ },
      async withdraw() { withdrew++; },
    };
    const o = createOrchestrator(inv(), { lifecycle: lc, join: lobbyJoin, pipeline: noopPipeline(), acts, aloneness: noAloneness });
    const res = await o.run();
    check('lobby-flood: failed(stopped) via withdraw', res.status === 'failed' && res.completionReason === 'stopped');
    check('lobby-flood: withdraw invoked EXACTLY once (200 leaves are idempotent)', withdrew === 1, `withdrew=${withdrew}`);
    check('lobby-flood: exactly one terminal', terminals(lc.events).length === 1, `got ${terminals(lc.events).length}`);
    check('lobby-flood: never reached active', !lc.events.some((e) => e.status === 'active'));
    check('lobby-flood: every event legal', allLegal(lc.events));
  }

  // ── (2) report flood: 500 rapid status reports serialise and still reach active → completed ──
  {
    const lc = sink();
    const { acts, flood } = capturingActs();
    const floodJoin: JoinDriver = {
      async join(report) {
        for (let i = 0; i < 500; i++) void report('awaiting_admission');  // fire-and-forget flood
        await report('active');
        flood(1);   // one leave AFTER active ends the run so we can assert it reached active + completed
        return 'admitted' as JoinOutcome;
      },
      onRemoval() { return () => { /* */ }; },
      async leave() { /* */ },
      async withdraw() { /* */ },
    };
    const o = createOrchestrator(inv(), {
      lifecycle: lc, join: floodJoin, pipeline: noopPipeline(), acts, aloneness: noAloneness,
    });
    const res = await o.run();
    check('report-flood: reached a clean completed', res.status === 'completed');
    check('report-flood: exactly one terminal', terminals(lc.events).length === 1);
    check('report-flood: every event legal despite 500 reports', allLegal(lc.events));
    check('report-flood: did reach active', lc.events.some((e) => e.status === 'active'));
  }

  // ── (3) failure under load: pipeline.start throws → LEAVE called (no ghost) + one failed ──
  {
    const lc = sink();
    let left = false;
    const failingPipeline: Pipeline = { async start() { throw new Error('capture OOM under load'); }, async stop() { /* */ } };
    const join: JoinDriver = {
      async join(report) { await report('active'); return 'admitted' as JoinOutcome; },
      onRemoval() { return () => { /* */ }; },
      async leave() { left = true; },
      async withdraw() { /* */ },
    };
    const o = createOrchestrator(inv(), { lifecycle: lc, join, pipeline: failingPipeline, acts: noActs, aloneness: noAloneness });
    const res = await o.run();
    check('fail-under-load: terminal failed', res.status === 'failed');
    check('fail-under-load: LEFT the meeting (no ghost participant)', left);
    check('fail-under-load: exactly one terminal', terminals(lc.events).length === 1);
    check('fail-under-load: every event legal', allLegal(lc.events));
  }

  console.log(failed === 0
    ? '\n✅ bot orchestrator stress: floods + failure-under-load stay legal, single-terminal, no ghost'
    : `\n❌ ${failed} stress check(s) failed`);
  if (failed > 0) process.exit(1);
}

void main();
