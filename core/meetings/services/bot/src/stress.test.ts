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
import { canTransition, type BotStatus, type LifecycleEvent } from './contracts.js';
import type { JoinDriver, JoinOutcome, Pipeline, LifecycleSink, ActsSource, AlonenessSource } from './ports.js';
import type { Invocation } from './config.js';

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
const noopPipeline = (): Pipeline => ({ async start() { /* */ }, async stop() { /* */ } });
// The orchestrator subscribes to acts AFTER it reaches active — so an acts source that fires on
// subscribe delivers its acts during the active phase (when the handler is wired). `nLeaves` floods.
const leavesOnSubscribe = (n: number): ActsSource => ({
  subscribe(handler) { for (let i = 0; i < n; i++) void handler({ action: 'leave' }); return () => { /* */ }; },
});
const noActs: ActsSource = { subscribe() { return () => { /* */ }; } };
const noAloneness: AlonenessSource = { onAlone() { return () => { /* */ }; } };

const terminals = (e: LifecycleEvent[]) => e.filter((x) => x.status === 'completed' || x.status === 'failed');
const allLegal = (e: LifecycleEvent[]) =>
  e.map((x) => x.status).every((st, i, s) => i === 0 || st === s[i - 1] || canTransition(s[i - 1], st));

async function main(): Promise<void> {
  console.log('\n=== bot orchestrator stress (shake) ===');

  // ── (1) act flood: 200 concurrent `leave` acts → exactly ONE clean terminal ──
  {
    const lc = sink();
    const o = createOrchestrator(inv(), {
      lifecycle: lc, join: mockJoin('admitted'), pipeline: noopPipeline(), acts: leavesOnSubscribe(200), aloneness: noAloneness,
    });
    const res = await o.run();  // 200 leave acts flood in on subscribe (during active)
    check('act-flood: completed(stopped)', res.status === 'completed' && res.completionReason === 'stopped');
    check('act-flood: EXACTLY one terminal (no double-terminal under 200 leaves)', terminals(lc.events).length === 1,
      `got ${terminals(lc.events).length}`);
    check('act-flood: every event legal', allLegal(lc.events));
  }

  // ── (2) report flood: 500 rapid status reports serialise and still reach active → completed ──
  {
    const lc = sink();
    const floodJoin: JoinDriver = {
      async join(report) {
        for (let i = 0; i < 500; i++) void report('awaiting_admission');  // fire-and-forget flood
        await report('active');
        return 'admitted' as JoinOutcome;
      },
      onRemoval() { return () => { /* */ }; },
      async leave() { /* */ },
      async withdraw() { /* */ },
    };
    const o = createOrchestrator(inv(), {
      lifecycle: lc, join: floodJoin, pipeline: noopPipeline(), acts: leavesOnSubscribe(1), aloneness: noAloneness,
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

function mockJoin(outcome: JoinOutcome): JoinDriver {
  return {
    async join(report) { await report('awaiting_admission'); if (outcome === 'admitted') await report('active'); return outcome; },
    onRemoval() { return () => { /* */ }; },
    async leave() { /* */ },
    async withdraw() { /* */ },
  };
}

void main();
