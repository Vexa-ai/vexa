/**
 * recency-tie.smoke — REGRESSION (live-captured 2026-08-25, 3-party Zoom call).
 *
 * When two speakers' hint turns both span a SHORT commit, their supportMs land
 * within RECENCY_TIE_MS of each other and confidence lands at ~0.5 — under
 * MIN_MATCH_CONFIDENCE (0.6). windowMatch's own tie-break had ALREADY picked the
 * speaker who just started ("a previous speaker's still-open turn can't out-vote
 * the speaker who actually just started"), and then the confidence floor discarded
 * that answer. Live evidence: 499 of 499 binder rejections were this exact shape —
 *   reject=confidence conf=0.50 min=0.6 coverage=1.00 candidates=2
 * — so the tie-break was dead code and every turn published unnamed.
 */
import { ClusterNameBinder } from './cluster-name-binder.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

{
  // Manolo is still lit (open turn, within grace); Diego starts 200ms later.
  const b = new ClusterNameBinder({ kindLagMs: { 'dom-active': 0 } });
  b.recordHint({ name: 'Manolo', tMs: 1000, kind: 'dom-active' });
  b.recordHint({ name: 'Diego',  tMs: 1200, kind: 'dom-active' });
  const r = b.resolve({ clusterId: 'seg_tie', tStartMs: 1500, tEndMs: 2600 });
  check('recency-resolved near-tie is not vetoed by the confidence floor',
    r.speakerName === 'Diego', `got ${r.speakerName} (${r.source}, conf=${r.confidence})`);
}

{
  // GUARD: a genuine 3-way ambiguity where recency does NOT separate the top two
  // must still refuse rather than stamp a name (the founder ruling: blank > wrong).
  const b = new ClusterNameBinder({ kindLagMs: { "dom-active": 0 } });
  b.recordHint({ name: "Ann", tMs: 1000, kind: "dom-active" });
  b.recordHint({ name: "Bob", tMs: 1000, kind: "dom-active" });
  const r = b.resolve({ clusterId: "seg_amb", tStartMs: 1500, tEndMs: 2600 });
  check("exact tie with no recency separation still refuses",
    r.source === "provisional-cluster-id", `got ${r.speakerName} (${r.source})`);
}

if (failed) { console.error(`\n❌ recency-tie: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ recency-tie: the tie-break survives the confidence floor.`);
