/**
 * tie-swamp.smoke — winner selection ranks by IN-SPAN evidence on its OWN scale.
 * Pins the pass-two regression (#868): once selection ranked by in-span ms it reused
 * RECENCY_TIE_MS (1000ms) as the tie band — but in-span slices are sub-second, so
 * EVERY pair fell inside the band and the more-recent hint won on recency, handing
 * the commit back to the lagging neighbour. The tie band now scales to the metric
 * (a fifth of the larger claim, floor 50ms), so recency only breaks a genuine tie.
 *
 * Scenario: a commit [10000,11000]. True speaker T lit 500ms of the span; neighbour
 * N lit only 200ms of it but started LATER (more recent). T is the larger in-span
 * claim and must win.
 *
 * Pre-fix (4a48649a): |500−200|=300 < 1000 tie band → recency → N wins → conf 0.286
 *   < gate → the turn resolves provisional (seg id).
 * Post-fix (head):   tie band = max(50, 0.2·500)=100; 500 > 200+100 → T wins → binds.
 */
import { ClusterNameBinder } from './index.js';

const b = new ClusterNameBinder({});
// T: dom-active [9500,10500] internal (lag 250) → 500ms in the commit span.
b.recordHint({ name: 'T', tMs: 9750, kind: 'dom-active' });
b.recordHint({ name: 'T', tMs: 10750, kind: 'dom-active', isEnd: true });
// N: dom-active [10800,11800] internal → 200ms in-span, but a MORE RECENT start.
b.recordHint({ name: 'N', tMs: 11050, kind: 'dom-active' });
b.recordHint({ name: 'N', tMs: 12050, kind: 'dom-active', isEnd: true });

const r = b.resolve({ clusterId: 'seg_7', tStartMs: 10000, tEndMs: 11000 });
console.log(`tie-swamp commit → speaker=${r.speakerName} source=${r.source} conf=${r.confidence?.toFixed(3)}`);
const ok = r.speakerName === 'T' && r.source === 'window-match';
console.log(ok
  ? '✅ PASS — the larger in-span claim (T) won; recency did not swamp the sub-second tie'
  : `❌ PASS-two regression — recency swamped the in-span tie and the neighbour took the commit (${r.speakerName})`);
process.exit(ok ? 0 : 1);
