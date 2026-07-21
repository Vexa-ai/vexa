/**
 * A meeting degraded by STT says so on the way out. OFFLINE, no browser/redis/whisper.
 *
 * The defect this pins: the pipelines type and attribute every STT fault and hand it to
 * `onError`, and the composition root logged it to a console that dies with the container — so a
 * meeting whose backend refused every chunk reached `completed` indistinguishable from a silent
 * room, which is exactly the zero-segment shape nobody could diagnose after the fact.
 *
 * Asserts the accumulator's contract AND the orchestrator wiring: the terminal lifecycle.v1 event
 * carries the summary, non-terminal events never do, a fault storm collapses to one report, and
 * neither a non-STT fault nor a throwing reporter can perturb the exit path.
 * Run: npx tsx src/stt-faults.test.ts
 */
import { createSttFaultReporter } from './stt-faults.js';
import { createOrchestrator } from './orchestrator.js';
import type { Invocation } from './config.js';
import type { LifecycleEvent } from './contracts.js';
import type { JoinDriver, Pipeline, ActsSource, LifecycleSink } from './ports.js';
import { noopAloneness } from './test-doubles.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

/** A TranscriptionError as @vexa/transcribe-whisper throws it (structurally). */
const sttError = (kind: string, status: number, detail: string) =>
  Object.assign(new Error(`stt ${kind} (HTTP ${status}): ${detail}`), { source: 'stt', kind, status, detail, retryable: false });

const inv = {
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'B',
  connectionId: 'conn-stt-1', redisUrl: 'redis://unused:6379', nativeMeetingId: 'abc-defg-hij',
} as Invocation;

function fakes(events: LifecycleEvent[]) {
  const lifecycle: LifecycleSink = { async emit(e) { events.push(e); } };
  const join: JoinDriver = {
    async join(report) { await report('awaiting_admission'); return 'admitted'; },
    onRemoval() { return () => { /* */ }; },
    async leave() { /* */ }, async withdraw() { /* */ },
  };
  const pipeline: Pipeline = { async start() { /* */ }, async stop() { /* */ } };
  const acts: ActsSource = { subscribe() { return () => { /* */ }; } };
  return { lifecycle, join, pipeline, acts, aloneness: noopAloneness() };
}

async function main(): Promise<void> {
  // ── 1) the accumulator: storm → ONE summary, counted per kind, backend's own words kept ──
  {
    const logs: string[] = [];
    const r = createSttFaultReporter((m) => logs.push(m), () => new Date('2026-07-19T12:00:00Z'));
    check('nothing degraded ⇒ nothing reported', r.report() === undefined);

    for (let i = 0; i < 18; i++) r.record(sttError('payment_required', 402, 'Insufficient balance. Available: 0.00 minutes'));
    r.record(sttError('unavailable', 503, 'upstream down'));

    const rep = r.report() as any;
    check('a degraded meeting reports', !!rep && !!rep.stt_fault);
    check('18 faults collapse to ONE summary entry per kind', rep.stt_fault.kinds.length === 2,
      JSON.stringify(rep.stt_fault.kinds.map((k: any) => k.kind)));
    const pay = rep.stt_fault.kinds.find((k: any) => k.kind === 'payment_required');
    check('the count carries the storm (18)', pay.count === 18, String(pay?.count));
    check('the backend HTTP status is kept', pay.status === 402, String(pay?.status));
    check("the backend's OWN detail is kept, not a paraphrase", /Insufficient balance/.test(pay.detail), pay?.detail);
    check('total across kinds', rep.stt_fault.total === 19, String(rep.stt_fault.total));
    check('kinds are ordered worst-first', rep.stt_fault.kinds[0].kind === 'payment_required');
    check('a human-readable reason rides the field lifecycle.v1 already has',
      /stt_degraded: payment_required×18/.test(rep.reason), rep.reason);
    check('only the FIRST of a kind logs loudly (a storm must not flood the log)',
      logs.length === 2, `${logs.length} lines`);
    check('the loud line names the consequence', /transcription is failing/.test(logs[0]), logs[0]);
  }

  // ── 2) a non-STT fault is not an STT degradation ──
  {
    const r = createSttFaultReporter(() => { /* quiet */ });
    r.record(new Error('some unrelated boom'));
    r.record(null);
    r.record(undefined);
    check('a fault with no STT provenance is ignored (and null/undefined never throw)',
      r.report() === undefined && r.total() === 0, String(r.total()));
  }

  // ── 3) the wiring: the TERMINAL event carries it; earlier events do not ──
  {
    const events: LifecycleEvent[] = [];
    const r = createSttFaultReporter(() => { /* quiet */ });
    const o = createOrchestrator(inv, { ...fakes(events), degraded: () => r.report() });
    r.record(sttError('payment_required', 402, 'Insufficient balance'));
    const run = o.run({ maxActiveMs: 50 });
    const result = await run;

    const terminal = events[events.length - 1] as any;
    const nonTerminal = events.slice(0, -1) as any[];
    check('the meeting still ends normally (reporting never changes the exit)',
      result.status === 'completed' && result.exitCode === 0, JSON.stringify(result));
    check('the TERMINAL event carries stt_fault', !!terminal.stt_fault,
      JSON.stringify(Object.keys(terminal)));
    check('it names the kind that broke the transcript',
      terminal.stt_fault.kinds[0].kind === 'payment_required', JSON.stringify(terminal.stt_fault));
    check('NO non-terminal event carries it (one report, at the end)',
      nonTerminal.every((e) => !e.stt_fault), JSON.stringify(nonTerminal.map((e) => e.status)));
  }

  // ── 4) a clean meeting is unchanged — no field, no noise ──
  {
    const events: LifecycleEvent[] = [];
    const r = createSttFaultReporter(() => { /* quiet */ });
    const o = createOrchestrator(inv, { ...fakes(events), degraded: () => r.report() });
    await o.run({ maxActiveMs: 50 });
    check('a meeting with no STT fault emits NO stt_fault field',
      events.every((e) => !(e as any).stt_fault), JSON.stringify(events.map((e) => e.status)));
  }

  // ── 5) a reporter that throws must not break the terminal emit ──
  {
    const events: LifecycleEvent[] = [];
    const o = createOrchestrator(inv, {
      ...fakes(events),
      degraded: () => { throw new Error('reporter exploded'); },
    });
    const result = await o.run({ maxActiveMs: 50 });
    check('a throwing reporter still lets the bot report its terminal state',
      result.status === 'completed' && events.some((e) => e.status === 'completed'),
      JSON.stringify(result));
  }

  if (failed) { console.error(`\n❌ stt-faults: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ stt-faults: a meeting degraded by STT carries WHY on its terminal lifecycle.v1 event — one summary per kind with the backend\'s own words, never on a non-terminal event, and never at the cost of the exit path.');
}

void main();
