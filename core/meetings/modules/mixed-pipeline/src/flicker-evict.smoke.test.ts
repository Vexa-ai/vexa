/**
 * flicker-evict.smoke — the flicker debounce keeps a slice that carries IN-SPAN
 * evidence, dropping only out-of-span transients. Pins the pass-two regression
 * (#868): the debounce evicted ANY closed slice shorter than FLICKER_MIN_MS (1000ms)
 * before ranking, so the true speaker's short sub-second slice DURING the commit was
 * thrown away and the window handed to the lagging neighbour — the sole survivor.
 *
 * Scenario: a commit [10000,10800]. True speaker T lit an 800ms slice fully inside
 * the span (shorter than FLICKER_MIN_MS). Neighbour N lit a 1350ms slice that ends
 * before the span (in the ±slack, zero in-span).
 *
 * Pre-fix (4a48649a): T's 800ms slice < 1000ms → flicker-skipped → only N remains →
 *   N carries no in-span → the turn resolves provisional (seg id).
 * Post-fix (head):   T's slice intersects the span → kept → T out-ranks N → binds.
 */
import { ClusterNameBinder } from './index.js';

const b = new ClusterNameBinder({});
// T: dom-active [10000,10800] internal (lag 250) → an 800ms slice, entirely in-span,
// shorter than the 1000ms flicker floor.
b.recordHint({ name: 'T', tMs: 10250, kind: 'dom-active' });
b.recordHint({ name: 'T', tMs: 11050, kind: 'dom-active', isEnd: true });
// N: dom-active [8550,9900] internal → 1350ms, ends before the span (in the ±slack).
b.recordHint({ name: 'N', tMs: 8800, kind: 'dom-active' });
b.recordHint({ name: 'N', tMs: 10150, kind: 'dom-active', isEnd: true });

const r = b.resolve({ clusterId: 'seg_9', tStartMs: 10000, tEndMs: 10800 });
console.log(`flicker-evict commit → speaker=${r.speakerName} source=${r.source} conf=${r.confidence?.toFixed(3)}`);
const ok = r.speakerName === 'T' && r.source === 'window-match';
console.log(ok
  ? '✅ PASS — the short in-span slice was kept; T bound instead of the out-of-span neighbour'
  : `❌ PASS-two regression — the in-span slice was flicker-evicted and the turn went provisional (${r.speakerName})`);
process.exit(ok ? 0 : 1);
