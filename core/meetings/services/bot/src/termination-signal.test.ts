/**
 * L2 — the bot says when its run was ended by a termination signal (SIGTERM/SIGINT), not a command.
 *
 * A bot in a live meeting that Kubernetes preempts, evicts or drains gets SIGTERM; its handler
 * leaves gracefully and reports `completed` / `stopped` — the same event a user's Stop produces
 * through the `leave` act. meeting-api cannot tell the two apart from the event, so a preempted
 * bot's meeting read as a user stop (the scal-1104 stand, 4 Nov 2026: a coredns replica preempted a
 * running bot). The bot is the one party that KNOWS which it was, so it says so, first-hand:
 *   • SIGTERM while active → `completed` / `stopped` carries `ended_by: "termination_signal"`;
 *   • SIGTERM while still in the lobby → the `failed` / `stopped` withdraw carries it too;
 *   • a `leave` act, host removal, aloneness and the time cap carry nothing new;
 *   • the first trigger wins: a SIGTERM after a `leave` act does not relabel the user's stop;
 *   • every event still conforms to the sealed lifecycle.v1 schema (the field is additive).
 * Run: npx tsx src/termination-signal.test.ts
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createOrchestrator } from './orchestrator.js';
import { installSignalHandlers } from './signals.js';
import type { LifecycleEvent } from './contracts.js';
import type { JoinDriver, JoinOutcome, LifecycleSink } from './ports.js';
import type { Invocation } from './config.js';
import { noopAloneness, controlledAloneness, noopPipeline, noopActs } from './test-doubles.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const HERE = dirname(fileURLToPath(import.meta.url));
const lcSchema = JSON.parse(readFileSync(
  join(HERE, '..', '..', '..', 'contracts', 'lifecycle.v1', 'lifecycle.schema.json'), 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(lcSchema);
const validateLifecycle: ValidateFunction = ajv.compile({ $ref: `${lcSchema.$id}#/$defs/LifecycleEvent` });

const inv = (): Invocation => ({
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'B',
  redisUrl: 'redis://r:6379', connectionId: 'sess-uid', container_name: 'mtg-abc123-bot',
  nativeMeetingId: 'abc-defg-hij',
});
const recordingSink = (): LifecycleSink & { readonly events: LifecycleEvent[] } => {
  const events: LifecycleEvent[] = [];
  return { events, async emit(e: LifecycleEvent) { events.push(e); } };
};
const admitted = (): JoinDriver => ({
  async join(report) { await report('awaiting_admission'); await report('active'); return 'admitted' as JoinOutcome; },
  onRemoval() { return () => { /* */ }; },
  async leave() { /* */ },
  async withdraw() { /* */ },
});
const lobby = (): JoinDriver => ({
  async join(report) { await report('awaiting_admission'); return new Promise<JoinOutcome>(() => { /* never */ }); },
  onRemoval() { return () => { /* */ }; },
  async leave() { /* */ },
  async withdraw() { /* */ },
});
const last = (e: LifecycleEvent[]) => e[e.length - 1] as LifecycleEvent & { ended_by?: string };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A fake `process` for installSignalHandlers: lets the test deliver SIGTERM. */
class FakeProc {
  handlers = new Map<string, Array<() => void>>();
  once(event: string, handler: () => void) { (this.handlers.get(event) ?? this.handlers.set(event, []).get(event)!).push(handler); return this; }
  off(event: string, handler: () => void) { this.handlers.set(event, (this.handlers.get(event) ?? []).filter((h) => h !== handler)); return this; }
  emit(event: string) { const hs = this.handlers.get(event) ?? []; this.handlers.set(event, []); for (const h of hs) h(); }
  exit(_code: number) { /* the watchdog; never reached inside these runs */ }
}

async function main(): Promise<void> {
  // ── SIGTERM while active, through the real signal handler → completed/stopped + ended_by ──
  {
    const lc = recordingSink();
    const o = createOrchestrator(inv(), { lifecycle: lc, join: admitted(), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const proc = new FakeProc();
    const release = installSignalHandlers({ stop: (r, opts) => o.stop(r, opts), graceMs: 60_000, proc, log: () => {} });
    const runP = o.run();
    await sleep(5);
    proc.emit('SIGTERM');
    const res = await runP;
    release();
    const ev = last(lc.events);
    check('SIGTERM active: completed(stopped)', res.status === 'completed' && ev.completion_reason === 'stopped');
    check('SIGTERM active: ended_by=termination_signal', ev.ended_by === 'termination_signal', JSON.stringify(ev));
    check('SIGTERM active: conforms to lifecycle.v1', validateLifecycle(ev) as boolean, ajv.errorsText(validateLifecycle.errors));
  }

  // ── a `leave` act (the user's Stop) → completed(stopped), no ended_by ──
  {
    const lc = recordingSink();
    let fireLeave: (a: { action: 'leave' }) => void = () => {};
    const o = createOrchestrator(inv(), { lifecycle: lc, join: admitted(), pipeline: noopPipeline(), acts: noopActs((f) => { fireLeave = f; }), aloneness: noopAloneness() });
    const runP = o.run();
    setTimeout(() => fireLeave({ action: 'leave' }), 5);
    await runP;
    check('leave act: completed(stopped) without ended_by', last(lc.events).completion_reason === 'stopped' && last(lc.events).ended_by === undefined,
      JSON.stringify(last(lc.events)));
  }

  // ── first trigger wins: leave act, then SIGTERM (the runtime tearing down a stopped bot) ──
  {
    const lc = recordingSink();
    let fireLeave: (a: { action: 'leave' }) => void = () => {};
    const o = createOrchestrator(inv(), { lifecycle: lc, join: admitted(), pipeline: noopPipeline(), acts: noopActs((f) => { fireLeave = f; }), aloneness: noopAloneness() });
    const runP = o.run();
    await sleep(5);
    fireLeave({ action: 'leave' });
    o.stop('stopped', { bySignal: true });
    await runP;
    check('leave then SIGTERM: the user stop stays unmarked', last(lc.events).ended_by === undefined, JSON.stringify(last(lc.events)));
  }

  // ── aloneness ends the run first; a later SIGTERM marks nothing ──
  {
    const lc = recordingSink();
    let fireAlone: () => void = () => {};
    const o = createOrchestrator(inv(), { lifecycle: lc, join: admitted(), pipeline: noopPipeline(), acts: noopActs(), aloneness: controlledAloneness((f) => { fireAlone = f; }) });
    const runP = o.run();
    await sleep(5);
    fireAlone();
    o.stop('stopped', { bySignal: true });
    await runP;
    check('left_alone then SIGTERM: completed(left_alone), no ended_by',
      last(lc.events).completion_reason === 'left_alone' && last(lc.events).ended_by === undefined, JSON.stringify(last(lc.events)));
  }

  // ── SIGTERM in the lobby → failed(stopped) withdraw carries ended_by too ──
  {
    const lc = recordingSink();
    const o = createOrchestrator(inv(), { lifecycle: lc, join: lobby(), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness() });
    const runP = o.run();
    await sleep(5);
    o.stop('stopped', { bySignal: true });
    const res = await runP;
    const ev = last(lc.events);
    check('SIGTERM lobby: failed(stopped)', res.status === 'failed' && ev.completion_reason === 'stopped');
    check('SIGTERM lobby: ended_by=termination_signal', ev.ended_by === 'termination_signal', JSON.stringify(ev));
    check('SIGTERM lobby: conforms to lifecycle.v1', validateLifecycle(ev) as boolean, ajv.errorsText(validateLifecycle.errors));
  }

  if (failed) {
    console.error(`\n${failed} check(s) failed`);
    process.exit(1);
  }
  console.log('\nall termination-signal checks passed');
}

main().catch((e) => { console.error(e); process.exit(1); });
