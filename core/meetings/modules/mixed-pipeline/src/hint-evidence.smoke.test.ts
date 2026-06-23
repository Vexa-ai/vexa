/**
 * hint-evidence.smoke — a speaker name is accepted only when the hint evidence
 * covers enough of the audio turn. A brief platform switch to another tile should
 * leave a long turn provisional instead of stamping a confident wrong name.
 */
import { ClusterNameBinder } from './index.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// A short Bob hint overlaps a long turn. Support is measured against the turn
// span, so the honest result is provisional.
{
  const b = new ClusterNameBinder({ kindLagMs: { 'dom-active': 0 } });
  b.recordHint({ name: 'Bob', tMs: 2000, kind: 'dom-active' });
  b.recordHint({ name: 'Bob', tMs: 3200, kind: 'dom-active', isEnd: true });
  const r = b.resolve({ clusterId: 'seg_long', tStartMs: 1000, tEndMs: 6000 });
  check('brief wrong hint does not name a long turn',
    r.source === 'provisional-cluster-id' && r.speakerName === 'seg_long',
    `${r.speakerName} (${r.source}, ${r.confidence})`);
}

// Sustained evidence still binds normally.
{
  const b = new ClusterNameBinder({ kindLagMs: { 'dom-active': 0 } });
  b.recordHint({ name: 'Alice', tMs: 1100, kind: 'dom-active' });
  b.recordHint({ name: 'Alice', tMs: 5600, kind: 'dom-active', isEnd: true });
  const r = b.resolve({ clusterId: 'seg_alice', tStartMs: 1000, tEndMs: 6000 });
  check('sustained hint names the turn',
    r.source === 'window-match' && r.speakerName === 'Alice',
    `${r.speakerName} (${r.source}, ${r.confidence})`);
}

if (failed) {
  console.error(`\n❌ hint-evidence: ${failed} check(s) FAILED.`);
  process.exit(1);
}
console.log('\n✅ hint-evidence: weak active-speaker switches stay provisional; sustained hints still bind.');
