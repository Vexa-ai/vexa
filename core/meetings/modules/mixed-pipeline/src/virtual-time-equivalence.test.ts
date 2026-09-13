/**
 * The virtual-time tier must be the SAME RUN as real time, not a faster approximation.
 *
 * Two things in this lane are wall-clock driven — the 1 s heartbeat that ticks, rolls and
 * TTL-finalizes the open turn, and the TTL's own comparison — and they are why a replay had to run
 * at 1x to reproduce anything cadence-shaped. The virtual tier drives those same paths from the
 * tape's timestamps and finishes in seconds; this test is what makes that claim checkable rather
 * than believed. It compares the DURABLE ROWS **and the whole publish stream**, drafts and
 * retractions included: a tier that agreed on the endpoint while taking a different route through
 * the draft/confirm cycle would be useless for exactly the defects it exists to catch.
 *
 * The tape is deliberately tiny (a few seconds), because this runs in the suite — the full proof on
 * the m24 fixture (478 s of tape, 12.6 s virtual) is an operation, recorded in the change that
 * introduced the tier. Even so this pays a few seconds of real time ONCE, which is the point: every
 * other replay in the loop no longer pays any.
 *
 * WHY THE SPEED CHECK DOES NOT COMPARE THE TWO RUNS. It used to assert `virtualMs < realMs`, and
 * that assertion measured the runner rather than the tier. The 1x run's wall clock is FLOORED by
 * the tape — ~8.5 s, and it barely moves under load — while the virtual run is pure compute, and it
 * is strictly MORE compute than the 1x run does, because every event and every heartbeat awaits a
 * drain that `--realtime` never performs. On a fast machine that costs ~1.5 s and the comparison
 * looks comfortable; on a 2-core CI runner it stretches past 8.5 s and the comparison inverts. It
 * inverted on PRs whose entire diff was one Markdown file (10035 vs 8672 ms, and 14687 vs 8496 ms),
 * which is the signature of a check that is not testing its own subject.
 *
 * So the speed claim is now stated against the thing it is actually about: the virtual run reports
 * how much LANE CLOCK it advanced, and must come in under that. A clock that genuinely slept could
 * not — it would need at least 1x, and the two CI runs above sit at 0.27x and 0.39x of the budget.
 * The companion check asserts the mechanism ran at all (a heartbeat for every second of lane time),
 * which the old wall-clock comparison never tested: a tier that fired NO heartbeats would have been
 * fast, and would have passed. `realMs` is still measured and printed, because it is useful when
 * reading a failure — it is simply never asserted on.
 *
 * Run: npx tsx src/virtual-time-equivalence.test.ts
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

const here = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), 'vexa-vt-'));
const T0 = 1786470000000;
const SR = 16000;
const TAPE_MS = 8000;

// Two speakers on the transport, alternating, long enough that the heartbeat fires several times
// within a turn — which is the only way a growing-window submission (and therefore a draft, and
// therefore a retraction) ever happens.
const runs: Array<[number, number, number]> = [[11, 0, 2600], [22, 3000, 5400], [11, 5800, 7600]];
const activeAt = (ms: number): number | null => {
  for (const [tr, a, b] of runs) if (ms >= a && ms < b) return tr;
  return null;
};
const tape: string[] = [JSON.stringify({
  type: 'captured_signal_header', v: 1, platform: 'teams', native_meeting_id: 'vt',
  language: null, lane: 'mixed', sample_rate: SR, started_at: new Date(T0).toISOString(), trace_id: 'vt',
})];
for (let ms = 0; ms < TAPE_MS; ms += 100) {
  const tr = activeAt(ms);
  if (tr === null) continue;
  const n = SR / 10;
  const buf = Buffer.alloc(n * 4);
  for (let i = 0; i < n; i++) buf.writeFloatLE(Math.sin((ms * SR / 1000 + i) / 7) * (tr === 11 ? 0.3 : 0.18), i * 4);
  tape.push(JSON.stringify({ ts: T0 + ms, pcm: buf.toString('base64'), seq: ms / 100, rms: 0.2 }));
}
for (const [tr, a] of runs) if (tr === 11) tape.push(JSON.stringify({ type: 'hint', t: T0 + a + 1000, name: 'Ana', isEnd: false, lane: 'mixed' }));
writeFileSync(join(dir, 'vt.captured-signal.jsonl'), tape.join('\n') + '\n');
writeFileSync(join(dir, 'vt.csrc.jsonl'), runs.flatMap(([tr, a, b]) => [
  JSON.stringify({ type: 'csrc', t: T0 + a, csrc: tr, active: true, lane: 'mixed' }),
  JSON.stringify({ type: 'csrc', t: T0 + b, csrc: tr, active: false, lane: 'mixed' }),
]).join('\n') + '\n');

const run = (mode: string, tag: string): { rows: string; writes: string; stdout: string } => {
  const stdout = execFileSync('npx', ['tsx', join(here, 'tape-replay.ts'),
    '--tape', join(dir, 'vt.captured-signal.jsonl'), '--turn-source', 'csrc', mode,
    '--out-json', join(dir, `${tag}.json`), '--out-writes', join(dir, `${tag}.writes.jsonl`)],
    { stdio: 'pipe', cwd: join(here, '..'), encoding: 'utf8' });
  return {
    rows: readFileSync(join(dir, `${tag}.json`), 'utf8'),
    writes: readFileSync(join(dir, `${tag}.writes.jsonl`), 'utf8'),
    stdout,
  };
};

const t0 = Date.now();
const virtual = run('--virtual-time', 'vt');
const virtualMs = Date.now() - t0;
const t1 = Date.now();
const real = run('--realtime', 'rt');
const realMs = Date.now() - t1;

// The virtual run reports its own mechanism — how far it moved the lane's clock, and how many
// heartbeats fired inside that span. Both assertions below read THOSE numbers; see the header for
// why neither one is allowed to look at how long the 1x run next to it happened to take.
const report = /virtual time: (\d+) heartbeat\(s\) fired, (\d+)ms of lane clock advanced/.exec(virtual.stdout);
const heartbeats = report ? Number(report[1]) : -1;
const advancedMs = report ? Number(report[2]) : -1;

check('the virtual run reproduces the real run\'s durable rows exactly',
  virtual.rows === real.rows, `virtual ${virtual.rows.length}B vs real ${real.rows.length}B`);
check('…and its whole publish stream, drafts and retractions included',
  virtual.writes === real.writes,
  `virtual ${virtual.writes.split('\n').length} calls vs real ${real.writes.split('\n').length}`);
check('the run actually exercised the draft/confirm cycle (otherwise it proves nothing)',
  real.writes.includes('"completed":false'), 'no draft was ever published — the tape is too short');
check('the virtual run drove the lane\'s clock over the whole tape, a heartbeat every second of it',
  advancedMs >= TAPE_MS && heartbeats >= Math.floor(advancedMs / 1000) - 1,
  `${heartbeats} heartbeat(s) across ${advancedMs}ms of lane clock — the tape alone is ${TAPE_MS}ms`);
check('…and it SKIPPED that time rather than sleeping through it',
  advancedMs > 0 && virtualMs < advancedMs,
  `${virtualMs}ms of wall clock to replay ${advancedMs}ms of lane clock` +
  ` (${(advancedMs / Math.max(virtualMs, 1)).toFixed(1)}x) — a clock that really slept could not beat 1x`);

if (failed) { console.error(`\n❌ virtual-time-equivalence: ${failed} check(s) FAILED.`); process.exit(1); }
console.log(`\n✅ virtual-time-equivalence: the tape's clock produced the identical run — ${heartbeats} heartbeat(s) over ` +
  `${advancedMs}ms of lane time — in ${virtualMs}ms of wall clock. (The 1x confirmation run beside it took ${realMs}ms; ` +
  `that number is reported, never asserted on.)`);
