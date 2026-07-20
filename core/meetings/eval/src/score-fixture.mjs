#!/usr/bin/env node
// score-fixture — measure a corpus entry now, and diff it against what it measured when promoted.
//
// This is the half of the corpus that makes it a REGRESSION suite rather than an archive. Two
// distinct comparisons, and the difference between them matters:
//
//   * the SIGNAL scores (delivery, shape, content) recompute from stored bytes, so they must
//     reproduce exactly. A drift there means the fixture or a scorer changed, not the pipeline.
//   * the LANE scores (--lane) re-run the real @vexa/mixed-pipeline over the fixture with a mock
//     STT. Everything is held fixed except OUR CODE, so any movement is the code moving — which is
//     precisely how a fixture built from a defect detects that defect coming back.
//
//   node src/score-fixture.mjs [<platform>/<slug> …]   (all entries if none named)
//     --lane      also re-run the mixed lane and diff its block
//     --update    rewrite baseline.json with what was just measured (deliberate re-baselining)
//
// Exit code 1 if anything moved beyond tolerance — so this is usable as a gate.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import { metricsFor, formatMetrics } from './signal-metrics.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const CORPUS = process.env.VEXA_CORPUS || path.join(homedir(), 'vexa-test-rig', 'fixtures');
const BOT = path.join(HERE, '..', '..', 'services', 'bot');

const argv = process.argv.slice(2);
const LANE = argv.includes('--lane');
const UPDATE = argv.includes('--update');
const named = argv.filter((a) => !a.startsWith('--'));

// Tolerances are per-metric because the metrics are not the same KIND of number: a duty cycle
// recomputed from the same bytes is exact, a word count is exact, but a wall-clock-derived p50 can
// wobble by a rounding step. Nothing here is loose enough to hide a real regression.
const TOL = { dutyCycle: 0.001, recall: 0.001, precision: 0.001, retention: 0.001, coverage: 0.02, segP50Sec: 0.05 };
const DEFAULT_TOL = 0;
// Where a metric has a direction, say so — it turns "changed" into "better" or "worse" in the
// report. Metrics with no entry are directionless: any movement is reported as a difference.
const HIGHER_IS_BETTER = new Set(['dutyCycle', 'recall', 'precision', 'retention', 'coverage']);
// `storeRows` and `segments` are deliberately absent: for a fixed fixture more rows can mean a
// duplication defect OR a session that was previously scored as one useless turn, and only the
// content tells them apart. Naming a direction there would assert a verdict the number cannot carry.
const LOWER_IS_BETTER = new Set(['storeDupes', 'holesOver2s', 'segUnder1s', 'gapCount', 'gapTotalSec', 'sttFails']);

function entries() {
  if (named.length) return named.map((n) => ({ id: n, dir: path.join(CORPUS, n) }));
  const out = [];
  for (const platform of readdirSync(CORPUS, { withFileTypes: true }).filter((d) => d.isDirectory())) {
    for (const slug of readdirSync(path.join(CORPUS, platform.name), { withFileTypes: true }).filter((d) => d.isDirectory())) {
      out.push({ id: `${platform.name}/${slug.name}`, dir: path.join(CORPUS, platform.name, slug.name) });
    }
  }
  return out;
}

function laneMetrics(sessionPath) {
  const tmp = path.join('/tmp', `lane-${createHash('sha1').update(sessionPath).digest('hex').slice(0, 8)}.json`);
  execFileSync('npx', ['tsx', 'src/quality-mixed.test.ts'], {
    cwd: BOT,
    // MOCK_STT makes the run deterministic and free. It says nothing about ASR quality and must
    // never be read as content evidence — it measures what the lane DOES with what it is handed.
    env: { ...process.env, QUALITY_MIXED_FIXTURE: sessionPath, MOCK_STT: '1', METRICS_JSON: tmp },
    stdio: ['ignore', 'ignore', 'inherit'],
  });
  const m = JSON.parse(readFileSync(tmp, 'utf8'));
  unlinkSync(tmp);
  return m;
}

function contentMetrics(dir) {
  const tmp = path.join(dir, '.score.tmp.json');
  execFileSync('python3', [
    path.join(HERE, 'single_pass_truth.py'), path.join(dir, 'session.captured-signal.jsonl.gz'),
    '--reference', path.join(dir, 'reference.txt'),
    '--realtime', path.join(dir, 'transcript.json'),
    '--json', tmp,
  ], { stdio: ['ignore', 'ignore', 'inherit'] });
  const m = JSON.parse(readFileSync(tmp, 'utf8'));
  unlinkSync(tmp);
  return m;
}

function diff(label, was, now) {
  const rows = [];
  for (const [k, before] of Object.entries(was ?? {})) {
    if (typeof before !== 'number') continue;
    const after = now?.[k];
    if (typeof after !== 'number') continue;
    const delta = after - before;
    if (Math.abs(delta) <= (TOL[k] ?? DEFAULT_TOL)) continue;
    const verdict = HIGHER_IS_BETTER.has(k) ? (delta > 0 ? 'better' : 'WORSE')
      : LOWER_IS_BETTER.has(k) ? (delta < 0 ? 'better' : 'WORSE')
      : 'changed';
    rows.push({ label, k, before, after, delta, verdict });
  }
  return rows;
}

let failed = 0;
for (const { id, dir } of entries()) {
  const manifestPath = path.join(dir, 'manifest.json');
  if (!existsSync(manifestPath)) { console.error(`${id}: no manifest.json — not a corpus entry`); failed++; continue; }
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const baseline = JSON.parse(readFileSync(path.join(dir, 'baseline.json'), 'utf8'));
  const sessionPath = path.join(dir, manifest.files.session);

  console.log(`\n${id}   ${manifest.note ? manifest.note.split('.')[0] + '.' : ''}`);

  // The fixture itself first. Every number below is a claim about THESE bytes; if they are not the
  // bytes the baseline was taken from, nothing after this line means anything.
  const sha = createHash('sha256').update(readFileSync(sessionPath)).digest('hex');
  if (sha !== manifest.sessionSha256) {
    console.error(`  ✗ session sha256 ${sha.slice(0, 12)}… != manifest ${manifest.sessionSha256.slice(0, 12)}… — the fixture changed under its own baseline`);
    failed++;
    continue;
  }

  const metrics = metricsFor(sessionPath);
  console.log(formatMetrics(metrics));
  let content = null, lane = null;
  if (manifest.files.reference && manifest.files.transcript) {
    content = contentMetrics(dir);
    console.log(`  content   recall ${content.recall} · precision ${content.precision} (${manifest.stt?.model ?? 'unknown model'}, single pass)`);
  }
  if (LANE) {
    if (manifest.lane !== 'mixed') {
      console.log(`  lane      skipped — ${manifest.lane} lane has its own harness (quality.test.ts)`);
    } else {
      lane = laneMetrics(sessionPath);
      console.log(`  lane      ${lane.storeRows} store rows (${lane.storeDupes} dup texts) · retention ${lane.retention} · coverage ${lane.coverage} · ${lane.holesOver2s} holes >2s`);
    }
  }

  const rows = [
    ...diff('delivery/shape', baseline.metrics, metrics),
    ...diff('content', baseline.content, content),
    ...diff('lane', baseline.lane, lane),
  ];
  if (rows.length) {
    console.log(`  ── vs baseline (${baseline.gitSha ?? '?'} @ ${baseline.recordedAt}) ──`);
    for (const r of rows) {
      console.log(`  ${r.verdict === 'WORSE' ? '✗' : r.verdict === 'better' ? '↑' : '·'} ${r.label}.${r.k}: ${r.before} → ${r.after} (${r.delta > 0 ? '+' : ''}${Number(r.delta.toFixed(3))}, ${r.verdict})`);
    }
    if (rows.some((r) => r.verdict !== 'better')) failed++;
  } else {
    console.log(`  ✓ matches its baseline (${baseline.gitSha ?? '?'})`);
  }

  if (UPDATE) {
    writeFileSync(path.join(dir, 'baseline.json'), JSON.stringify({
      recordedAt: new Date().toISOString(),
      gitSha: (() => { try { return execFileSync('git', ['-C', HERE, 'rev-parse', '--short', 'HEAD'], { encoding: 'utf8' }).trim(); } catch { return null; } })(),
      metrics, ...(content ? { content } : {}), ...(lane ? { lane } : {}),
    }, null, 2) + '\n');
    console.log(`  baseline.json rewritten`);
  }
}

process.exit(UPDATE ? 0 : failed ? 1 : 0);
