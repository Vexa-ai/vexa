#!/usr/bin/env node
/**
 * verdict.mjs — the autonomous PASS/FAIL oracle for a standalone-bot run.
 *
 * Aggregates the two REUSED eval scorers against `meetings/eval/BASELINE.md` and emits ONE verdict:
 *   • analyze.mjs  → the `SCORE … segments= segN= midcut= dup= short= misattr= [hijack=]` line
 *                    (segmentation + attribution health, offline oracle — no ground truth needed).
 *   • judge.py     → the `JUDGE completeness= leakage= attribution_pct= …` line
 *                    (vs the driven ground truth `truth.jsonl`; only when truth exists).
 * Both read the SAME `{segments}` transcript file (the standalone bot's transcript.v1, dumped from
 * redis by read-redis-transcript.mjs) via TRANSCRIPT_FILE — no gateway, no live meeting.
 *
 * Gate (BASELINE.md, gmeet lane):
 *   HARD  misattr=0 · dup=0 · seg_N=0 (gmeet fully bound) · leakage=0 · hijack=0 (noise lane)
 *   SOFT  midcut/segments ≤ VERDICT_MIDCUT_MAX (gmeet 10% · mixed 20%)
 *   (completeness + attribution_pct are REPORTED, not hard-gated — Learning #18: attribution
 *    over-counts under /speak latency; gate on it only via VERDICT_COMPLETENESS_MIN if you opt in.)
 *
 * Usage:  node verdict.mjs <transcript.json> [flags-out.json]
 *   env:  PLATFORM (google_meet) · NATIVE_ID · LANE (gmeet|mixed) · TRUTH_LOG · VIEWER (post banner)
 *         VERDICT_MIDCUT_MAX · VERDICT_COMPLETENESS_MIN · VEXA_NOISE_NAME (noise lane → analyze hijack)
 * Exits 0 on PASS, 1 on FAIL (so run.sh / CI gate on it). Also POSTs {pass,line,metrics} to VIEWER.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVAL = join(HERE, '..', '..', '..', 'eval');
const EVAL_SRC = join(EVAL, 'src');

const TRANSCRIPT = process.argv[2];
const FLAGS_OUT = process.argv[3] || null;     // optional: analyze --flag-out target (for attribute.mjs)
if (!TRANSCRIPT) { console.error('usage: verdict.mjs <transcript.json> [flags-out.json]'); process.exit(2); }
if (!existsSync(TRANSCRIPT)) { console.error(`[verdict] transcript ${TRANSCRIPT} not found`); process.exit(2); }

const PLATFORM = process.env.PLATFORM || 'google_meet';
const NATIVE = process.env.NATIVE_ID || 'standalone';
const LANE = process.env.LANE || (PLATFORM === 'google_meet' ? 'gmeet' : 'mixed');
const TRUTH = process.env.TRUTH_LOG || join(EVAL, 'truth.jsonl');
const VIEWER = process.env.VIEWER || null;
const MIDCUT_MAX = process.env.VERDICT_MIDCUT_MAX != null
  ? Number(process.env.VERDICT_MIDCUT_MAX) : (LANE === 'gmeet' ? 0.10 : 0.20);
const COMPLETENESS_MIN = process.env.VERDICT_COMPLETENESS_MIN != null ? Number(process.env.VERDICT_COMPLETENESS_MIN) : null;

// ── 1) analyze.mjs (always) — the offline segmentation/attribution oracle ──
const analyzeArgs = [join(EVAL_SRC, 'analyze.mjs'), PLATFORM, NATIVE];
if (FLAGS_OUT) analyzeArgs.push('--flag-issues', '--flag-out', FLAGS_OUT);
const a = spawnSync('node', analyzeArgs, { encoding: 'utf8', env: { ...process.env, TRANSCRIPT_FILE: TRANSCRIPT } });
const aout = (a.stdout || '') + (a.stderr || '');
process.stdout.write(aout.endsWith('\n') ? aout : aout + '\n');

const scoreM = aout.match(/SCORE \S+ segments=(\d+) segN=(\d+) midcut=(\d+) dup=(\d+) short=(\d+) misattr=(\d+)(?: hijack=(\d+))?/);
const score = scoreM ? {
  segments: +scoreM[1], segN: +scoreM[2], midcut: +scoreM[3], dup: +scoreM[4],
  short: +scoreM[5], misattr: +scoreM[6], hijack: scoreM[7] != null ? +scoreM[7] : null,
} : null;

// ── 2) judge.py (only if ground truth exists) — completeness/leakage/attribution vs truth.jsonl ──
let judge = null;
if (existsSync(TRUTH) && statSync(TRUTH).size > 0) {
  const j = spawnSync('python3', [join(EVAL_SRC, 'judge.py')], {
    encoding: 'utf8',
    env: { ...process.env, TRANSCRIPT_FILE: TRANSCRIPT, TRUTH_LOG: TRUTH, NATIVE_ID: NATIVE, PLATFORM },
  });
  const jout = (j.stdout || '') + (j.stderr || '');
  process.stdout.write('\n' + (jout.endsWith('\n') ? jout : jout + '\n'));
  const jm = jout.match(/JUDGE completeness=(\d+)\/(\d+) completeness_pct=(\d+) leakage=(\d+) leakage_pct=(\d+) attribution_pct=(\d+) wrong=(\d+) unknown=(\d+) named=(\d+) truth_turns=(\d+)/);
  if (jm) judge = {
    completeness: +jm[1], truth_turns_cov: +jm[2], completeness_pct: +jm[3],
    leakage: +jm[4], leakage_pct: +jm[5], attribution_pct: +jm[6], wrong: +jm[7], unknown: +jm[8], named: +jm[9],
  };
} else {
  console.log(`\n[verdict] no ground truth (${TRUTH}) — scoring analyze-only (drive the speakers to get completeness/leakage).`);
}

// ── 3) gate vs BASELINE.md ──
const fails = [];
if (!score || score.segments === 0) {
  fails.push('0 confirmed segments — the bot captured/transcribed NOTHING (join · capture · or STT, NOT attribution)');
} else {
  if (score.misattr > 0) fails.push(`misattr=${score.misattr} (HARD — content self-ID ≠ speaker label)`);
  if (score.dup > 0) fails.push(`dup=${score.dup} (HARD — boundary-word duplication)`);
  if (LANE === 'gmeet' && score.segN > 0) fails.push(`seg_N=${score.segN} (HARD — gmeet must be fully speaker-bound)`);
  if (score.hijack != null && score.hijack > 0) fails.push(`hijack=${score.hijack} (HARD — noise-bot label leaked into the transcript)`);
  const ratio = score.midcut / score.segments;
  if (ratio > MIDCUT_MAX) fails.push(`oversegmentation ${score.midcut}/${score.segments}=${(100 * ratio).toFixed(0)}% > ${(100 * MIDCUT_MAX).toFixed(0)}% (${LANE})`);
}
if (judge) {
  if (judge.leakage > 0) fails.push(`leakage=${judge.leakage} (HARD — segment content self-IDs a different speaker than its label)`);
  if (COMPLETENESS_MIN != null && judge.completeness_pct < COMPLETENESS_MIN) fails.push(`completeness ${judge.completeness_pct}% < ${COMPLETENESS_MIN}%`);
}

const pass = fails.length === 0 && !!score && score.segments > 0;

// ── 4) one-line verdict (stdout + viewer banner) ──
const parts = [];
if (score) parts.push(`segments=${score.segments}`, `misattr=${score.misattr}`, `dup=${score.dup}`, `seg_N=${score.segN}`, `midcut=${score.midcut}/${score.segments}`);
if (score && score.hijack != null) parts.push(`hijack=${score.hijack}`);
if (judge) parts.push(`completeness=${judge.completeness_pct}%`, `leakage=${judge.leakage}`, `attribution=${judge.attribution_pct}%`);
const line = parts.join(' ');

console.log(`\nVERDICT ${pass ? 'PASS' : 'FAIL'} [${LANE} · ${PLATFORM}/${NATIVE}] ${line}`);
if (!pass) {
  console.log('  reasons:');
  for (const f of fails) console.log(`    ✗ ${f}`);
  console.log(`  → which module? run:  node ${join(HERE, 'attribute.mjs')} ${FLAGS_OUT || TRANSCRIPT}`);
}

if (VIEWER) {
  try {
    await fetch(`${VIEWER.replace(/\/+$/, '')}/verdict`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ pass, line: `${pass ? 'PASS' : 'FAIL'} · ${line}`, metrics: { score, judge, fails } }),
    });
  } catch (e) { console.error(`[verdict] viewer POST failed: ${e.message}`); }
}

process.exit(pass ? 0 : 1);
