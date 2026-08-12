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
for (let ms = 0; ms < 8000; ms += 100) {
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

const run = (mode: string, tag: string): { rows: string; writes: string } => {
  execFileSync('npx', ['tsx', join(here, 'tape-replay.ts'),
    '--tape', join(dir, 'vt.captured-signal.jsonl'), '--turn-source', 'csrc', mode,
    '--out-json', join(dir, `${tag}.json`), '--out-writes', join(dir, `${tag}.writes.jsonl`)],
    { stdio: 'pipe', cwd: join(here, '..') });
  return { rows: readFileSync(join(dir, `${tag}.json`), 'utf8'), writes: readFileSync(join(dir, `${tag}.writes.jsonl`), 'utf8') };
};

const t0 = Date.now();
const virtual = run('--virtual-time', 'vt');
const virtualMs = Date.now() - t0;
const t1 = Date.now();
const real = run('--realtime', 'rt');
const realMs = Date.now() - t1;

check('the virtual run reproduces the real run\'s durable rows exactly',
  virtual.rows === real.rows, `virtual ${virtual.rows.length}B vs real ${real.rows.length}B`);
check('…and its whole publish stream, drafts and retractions included',
  virtual.writes === real.writes,
  `virtual ${virtual.writes.split('\n').length} calls vs real ${real.writes.split('\n').length}`);
check('the run actually exercised the draft/confirm cycle (otherwise it proves nothing)',
  real.writes.includes('"completed":false'), 'no draft was ever published — the tape is too short');
check(`and it did so without paying the wall clock (${virtualMs}ms vs ${realMs}ms)`,
  virtualMs < realMs, `${virtualMs}ms vs ${realMs}ms`);

if (failed) { console.error(`\n❌ virtual-time-equivalence: ${failed} check(s) FAILED.`); process.exit(1); }
console.log(`\n✅ virtual-time-equivalence: the tape's clock produces the identical run in ${virtualMs}ms that the wall clock takes ${realMs}ms to produce.`);
