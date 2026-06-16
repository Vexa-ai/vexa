/**
 * priority.smoke — a stale OPEN hint must decay (OPEN_TURN_GRACE_MS) so the previous
 * speaker can't keep out-voting a new speaker's later turns. Within the grace the
 * speaker still resolves; well beyond it (no heartbeat refresh) the turn is left
 * provisional rather than wrongly attributed to the lingering previous speaker.
 */
import { ClusterNameBinder } from './index';

const b = new ClusterNameBinder({});
b.recordHint({ name: 'Anna', tMs: 1000, kind: 'dom-active' });   // Anna lit; no successor → open turn

// Within the grace window → Anna still owns it.
const near = b.resolve({ clusterId: 's1', tStartMs: 2000, tEndMs: 3000 });
// 7s later, no refresh → Anna's open hint has decayed → NOT attributed to Anna.
const far = b.resolve({ clusterId: 's2', tStartMs: 8000, tEndMs: 9000 });

console.log(`near → ${near.speakerName} (${near.source})   far → ${far.speakerName} (${far.source})`);
const ok = near.speakerName === 'Anna' && far.source === 'provisional-cluster-id';
console.log(ok
  ? '✅ PASS — stale hint decays; a lingering previous speaker no longer blocks the new one'
  : '❌ FAIL — stale hint still claims a far-later turn (previous-speaker bleed)');
process.exit(ok ? 0 : 1);
