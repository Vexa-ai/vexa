/**
 * Attendance is a timeline, not a headcount. OFFLINE — no browser, no redis, no meeting.
 *
 * The defect this pins: every platform's speakers module already enumerates named participant
 * tiles on its live loop, and the bot consumed that only for "who is talking" and dropped the
 * rest — so a completed meeting could say what was said but never who was in the room, and a
 * late joiner or an early leaver was indistinguishable from someone present throughout.
 *
 * Asserts the accumulator's contract AND the orchestrator wiring: rejoins do not swallow the gap
 * they were away for, a participant still present at the end is measured to the end, the report
 * rides ONLY the terminal lifecycle.v1 event, the payload stays small enough to live in
 * `meeting.data` (the sibling per-tick channel measured 155 MB across one account), and a
 * throwing reporter cannot perturb the exit path.
 * Run: npx tsx src/attendance.test.ts
 */
import { createAttendanceReporter } from './attendance.js';
import { createOrchestrator } from './orchestrator.js';
import type { Invocation } from './config.js';
import type { LifecycleEvent } from './contracts.js';
import type { JoinDriver, Pipeline, ActsSource, LifecycleSink, AlonenessSource } from './ports.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const T0 = Date.parse('2026-07-27T10:00:00.000Z');
const min = (n: number) => T0 + n * 60_000;

const inv = {
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'B',
  connectionId: 'conn-att-1', redisUrl: 'redis://unused:6379', nativeMeetingId: 'abc-defg-hij',
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
  const aloneness: AlonenessSource = { onAlone() { return () => { /* */ }; } };
  return { lifecycle, join, pipeline, acts, aloneness };
}

const nameOf = (rep: any, name: string) =>
  rep.attendance.participants.find((p: any) => p.name === name);

async function main(): Promise<void> {
  // ── 1) nothing observed ⇒ nothing reported (an unattended meeting invents no roster) ──
  {
    const r = createAttendanceReporter(() => min(0));
    check('no roster ever seen ⇒ no report', r.report() === undefined);
    r.observe(['', '   ']);
    check('junk-only roster ⇒ still no report', r.report() === undefined);
  }

  // ── 2) late join + early leave + still-present-at-end ──
  {
    const r = createAttendanceReporter();
    r.observe(['Priya'], min(0));                    // Priya from the top
    r.observe(['Priya', 'Marcus'], min(30));         // Marcus joins late
    r.observe(['Priya'], min(45));                   // Marcus leaves early
    const rep = r.report(min(60)) as any;            // meeting ends at +60

    const priya = nameOf(rep, 'Priya');
    const marcus = nameOf(rep, 'Marcus');
    check('everyone seen is reported', rep.attendance.participants.length === 2);
    check('still-present participant is measured to the END, not to their arrival',
      priya.present_seconds === 3600, `got ${priya.present_seconds}`);
    check('still-present participant last_seen == meeting end',
      priya.last_seen === new Date(min(60)).toISOString(), priya.last_seen);
    check('late joiner is credited from arrival, not from meeting start',
      marcus.first_seen === new Date(min(30)).toISOString(), marcus.first_seen);
    check('early leaver is measured to their DEPARTURE, not to meeting end',
      marcus.present_seconds === 900, `got ${marcus.present_seconds}`);
    check('participants are ordered by arrival', rep.attendance.participants[0].name === 'Priya');
  }

  // ── 3) leave-and-return: the away gap is NOT counted ──
  {
    const r = createAttendanceReporter();
    r.observe(['Sam'], min(0));
    r.observe([], min(10));            // Sam drops
    r.observe(['Sam'], min(40));       // …and comes back 30 minutes later
    const rep = r.report(min(50)) as any;
    const sam = nameOf(rep, 'Sam');

    check('a rejoin opens a SECOND interval', sam.intervals.length === 2, JSON.stringify(sam.intervals));
    check('the 30-minute absence is excluded from present_seconds',
      sam.present_seconds === 1200, `got ${sam.present_seconds} (would be 3000 if the gap leaked)`);
    check('first_seen is the FIRST arrival', sam.first_seen === new Date(min(0)).toISOString());
    check('last_seen is the LAST presence', sam.last_seen === new Date(min(50)).toISOString());
  }

  // ── 4) the size budget — this payload lives in meeting.data, which is read on every list ──
  {
    const r = createAttendanceReporter();
    const people = ['Priya Raman', 'Marcus Webb', 'Sofia Delgado', 'Kenji Watanabe'];
    // 90 minutes of a realistically churny meeting: each person leaves and returns twice.
    r.observe(people, min(0));
    for (let i = 1; i <= 2; i++) {
      for (const p of people) {
        r.observe(people.filter((x) => x !== p), min(i * 20));
        r.observe(people, min(i * 20 + 2));
      }
    }
    const bytes = JSON.stringify(r.report(min(90))).length;
    check('90min × 4 people with churn serializes under 4KB', bytes < 4096, `${bytes} bytes`);
  }

  // ── 5) the wiring: the report rides the TERMINAL event, and only that one ──
  {
    const events: LifecycleEvent[] = [];
    const r = createAttendanceReporter();
    r.observe(['Priya'], min(0));
    const o = createOrchestrator(inv, { ...fakes(events), terminalExtras: () => r.report(min(60)) });
    await o.run({ maxActiveMs: 50 });

    const terminal = events[events.length - 1] as any;
    const nonTerminal = events.slice(0, -1) as any[];
    check('the terminal event carries attendance', !!terminal.attendance, JSON.stringify(terminal));
    check('attendance names the participant', terminal.attendance.participants[0].name === 'Priya');
    check('no NON-terminal event carries attendance', nonTerminal.every((e) => e.attendance === undefined),
      JSON.stringify(nonTerminal));
  }

  // ── 6) a throwing reporter must never change the exit path (P18) ──
  {
    const events: LifecycleEvent[] = [];
    const o = createOrchestrator(inv, {
      ...fakes(events),
      terminalExtras: () => { throw new Error('reporter exploded'); },
    });
    const res = await o.run({ maxActiveMs: 50 });
    check('a throwing reporter still reaches a terminal state', events.length > 0 && res.status === 'completed',
      `status=${res.status}`);
    check('…and the terminal event simply carries no attendance',
      (events[events.length - 1] as any).attendance === undefined);
  }

  if (failed) { console.error(`\n❌ attendance: ${failed} check(s) failed`); process.exit(1); }
  console.log('\n✅ attendance: who was in the room and for how long rides the terminal lifecycle.v1 event — intervals, so a rejoin never swallows the gap, a still-present participant is measured to the end, and the payload stays small enough to live in meeting.data.');
}

main().catch((e) => { console.error(e); process.exit(1); });
