/**
 * transcript-gaps — a lost window reaches the MEETING RECORD (prod-cutover R4, part 2).
 *
 * The per-channel lane has recorded missing ranges since stt-backoff, and the mixed lane now does
 * too — but nothing outside the lane read them, so a gap never left the container. This pins the
 * last hop: the lanes' ranges ride the TERMINAL lifecycle.v1 event as `transcript_gaps` (additive,
 * like `stt_fault`), and a meeting with no loss carries no such field.
 *
 *   tsx src/transcript-gaps.test.ts
 */
import { transcriptGapsReport, mergeDegraded } from './transcript-gaps.js';
import { createOrchestrator } from './orchestrator.js';
import { createBotPipeline } from './pipeline.js';
import type { Invocation } from './config.js';
import type { LifecycleEvent } from './contracts.js';
import type { ActsSource, JoinDriver, LifecycleSink, TranscriptSink } from './ports.js';
import { noopAloneness } from './test-doubles.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const T0 = 1_760_000_000_000;
const lostTeams = { speakerName: 'CSRC 7', startMs: T0, endMs: T0 + 2_000, reason: 'r' };

// ── the fragment ──
{
  check('no ranges → no fragment (a healthy meeting carries no field)', transcriptGapsReport([]) === undefined);
  const rep = transcriptGapsReport([
    { speakerName: 'Anna', startMs: T0 + 60_000, endMs: T0 + 75_500, reason: 'stt refused the window after 4 attempt(s) (unavailable HTTP 503)' },
    { speakerName: 'Bob', startMs: T0, endMs: T0 + 3_000, reason: 'held-audio bound' },
  ]) as any;
  const gaps = rep?.transcript_gaps;
  check('ranges become transcript_gaps, ordered by start', Array.isArray(gaps) && gaps.length === 2 && gaps[0].speaker === 'Bob', JSON.stringify(gaps));
  check('times are epoch seconds like transcript.v1 segments', gaps?.[1]?.start === (T0 + 60_000) / 1000 && gaps?.[1]?.end === (T0 + 75_500) / 1000, JSON.stringify(gaps?.[1]));
  check('absolute ISO times are stamped', gaps?.[1]?.absolute_start_time === new Date(T0 + 60_000).toISOString()
    && gaps?.[1]?.absolute_end_time === new Date(T0 + 75_500).toISOString(), JSON.stringify(gaps?.[1]));
  check('the reason travels', /503/.test(gaps?.[1]?.reason ?? ''));
  const many = transcriptGapsReport(Array.from({ length: 900 }, (_, i) => ({ speakerName: 'x', startMs: T0 + i * 10_000, endMs: T0 + i * 10_000 + 500, reason: 'r' }))) as any;
  check('bounded: at most 500 entries on one event', many.transcript_gaps.length === 500, String(many.transcript_gaps.length));
  check('mergeDegraded keeps stt_fault and adds gaps', (() => {
    const m = mergeDegraded({ stt_fault: { total: 1 }, reason: 'stt_degraded: unavailable×1' }, rep) as any;
    return m.stt_fault.total === 1 && m.reason.startsWith('stt_degraded') && m.transcript_gaps.length === 2;
  })());
  check('mergeDegraded of nothing is nothing', mergeDegraded(undefined, undefined) === undefined);
}

// ── end to end through the bot pipeline + the orchestrator: the gap is on the terminal event ──
// The mixed (Jitsi) lane — the R4 lane — behind its construction seam: the transcriber reports one
// window it could not transcribe (the real one is pinned in @vexa/mixed-pipeline missing-window).
{
  const inv = {
    platform: 'jitsi', meetingUrl: 'https://meet.ffmuc.net/r4room', botName: 'B',
    connectionId: 'conn-gaps-1', redisUrl: 'redis://unused:6379', nativeMeetingId: 'r4room',
  } as Invocation;
  const lost = { speakerName: 'Anna', startMs: T0 + 1_000, endMs: T0 + 4_000,
    reason: 'stt refused the window after 4 attempt(s) (unavailable HTTP 503)' };
  let disposed = false;
  const sink = { async publish() { /* */ } } as unknown as TranscriptSink;
  const engine = createBotPipeline(inv, sink, {
    transcribe: (async () => ({ text: '', language: 'en', segments: [] })) as any,
    createMixedTranscriber: async () => ({
      feedAudio() { /* */ }, recordHint() { /* */ },
      async dispose() { disposed = true; },
      missingRanges: () => (disposed ? [lost] : []),
    }) as any,
  });
  const events: LifecycleEvent[] = [];
  const lifecycle: LifecycleSink = { async emit(e) { events.push(e); } };
  const join: JoinDriver = {
    async join(report) { await report('awaiting_admission'); return 'admitted'; },
    onRemoval() { return () => { /* */ }; },
    async leave() { /* */ }, async withdraw() { /* */ },
  };
  const acts: ActsSource = { subscribe() { return () => { /* */ }; } };
  const o = createOrchestrator(inv, {
    lifecycle, join, pipeline: engine, acts, aloneness: noopAloneness(),
    degraded: () => mergeDegraded(undefined, transcriptGapsReport(engine.missingRanges?.() ?? [])),
  });
  const result = await o.run({ maxActiveMs: 50 });
  const terminal = events[events.length - 1] as any;
  check('the meeting still ends normally', result.status === 'completed', JSON.stringify(result));
  check('ranges are read AFTER the lane is disposed (the final pass decides)', disposed);
  check('the TERMINAL event carries transcript_gaps', Array.isArray(terminal?.transcript_gaps) && terminal.transcript_gaps.length === 1,
    JSON.stringify(terminal));
  check('non-terminal events never carry it', events.slice(0, -1).every((e) => !(e as any).transcript_gaps));
  const g = terminal?.transcript_gaps?.[0];
  check('the gap is the lost window, in epoch seconds', !!g && g.start === (T0 + 1_000) / 1000 && g.end === (T0 + 4_000) / 1000
    && g.speaker === 'Anna' && /503/.test(g.reason), JSON.stringify(g));
}

// ── every lane exposes the reader ──
{
  const base = { meetingUrl: 'x', botName: 'B', connectionId: 'c', redisUrl: 'redis://unused:6379', nativeMeetingId: 'n' };
  const sink = { async publish() { /* */ } } as unknown as TranscriptSink;
  const t = (async () => ({ text: '', language: 'en', segments: [] })) as any;
  const gmeet = createBotPipeline({ ...base, platform: 'google_meet' } as Invocation, sink, { transcribe: t });
  check('per-channel lane: missingRanges() reads the lane (empty when nothing lost)', Array.isArray(gmeet.missingRanges?.()) && gmeet.missingRanges!().length === 0);
  const teams = createBotPipeline({ ...base, platform: 'teams' } as Invocation, sink, {
    transcribe: t,
    createTeamsTranscriber: () => ({ missingRanges: () => [lostTeams] } as any),
  });
  check('teams lane: missingRanges() reads the CSRC lane', teams.missingRanges?.()?.[0] === lostTeams);
  await gmeet.stop();
}

// ── STOP BUDGET: a lane still waiting on the STT at stop is abandoned, not waited on forever ──
// The runtime destroys a stopping bot after STOP_RECONCILE_GRACE_S (45 s); a bot still draining a
// saturated STT then never sends its terminal event, and every gap dies with it.
{
  const inv = {
    platform: 'jitsi', meetingUrl: 'https://meet.ffmuc.net/r4room', botName: 'B',
    connectionId: 'conn-gaps-2', redisUrl: 'redis://unused:6379', nativeMeetingId: 'r4room',
  } as Invocation;
  const ranges: any[] = [];
  const sink = { async publish() { /* */ } } as unknown as TranscriptSink;
  const engine = createBotPipeline(inv, sink, {
    transcribe: (async () => ({ text: '', language: 'en', segments: [] })) as any,
    stopBudgetMs: 200,
    createMixedTranscriber: async () => ({
      feedAudio() { /* */ }, recordHint() { /* */ },
      dispose: () => new Promise<void>(() => { /* the STT never answers */ }),
      abandon: (reason: string) => { ranges.push({ speakerName: 'Anna', startMs: T0, endMs: T0 + 9_000, reason }); },
      missingRanges: () => ranges.slice(),
    }) as any,
  } as any);
  await engine.start();
  const t0 = Date.now();
  await engine.stop();
  const took = Date.now() - t0;
  check('stop() returns at the budget instead of waiting on the STT', took < 1500, `${took} ms`);
  check('what was still owed is recorded as missing, naming the budget',
    (engine.missingRanges?.() ?? []).length === 1 && /stop budget/.test(engine.missingRanges!()[0].reason), JSON.stringify(engine.missingRanges?.()));
}

if (failed) { console.error(`\n❌ transcript-gaps: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ transcript-gaps: a window the lane never transcribed reaches the meeting record on the terminal event.');
