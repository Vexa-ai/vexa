/**
 * handover.smoke — the confidence gate must judge the COMMIT SPAN, not the ±slack
 * neighbourhood. Two speakers alternate on one dom-active stream (heartbeat
 * re-assertions ~2s apart). At a handover, Ada lingers a beat while Ben takes over;
 * the diarizer cuts a short commit that Ben covers in-span. Ben is the in-span
 * winner AND holds the most support — yet the OLD confidence (a name's share of all
 * hint time in the ±support slack) let Ada's lingering slice dilute Ben's share
 * below the 0.6 gate, so the turn published provisional (seg_N) though the live UI
 * named it Ben.
 *
 * Pre-fix (f4461a85): windowMatch rejects on `confidence` (0.559) → provisional.
 * Post-fix (#868):     confidence is in-span contested-ness (0.643) → Ben binds.
 *
 * Anchored on the botsig9 real-STT red (#868): the ±slack neighbour vote is the
 * point of introduction; the fix removes the slack's vote while keeping its jitter
 * roles (admission, support, coverage).
 */
import { ClusterNameBinder } from './index.js';

const b = new ClusterNameBinder({});
// Ada speaks, heartbeating on dom-active every ~2s, then hands over — lingering a
// beat into the handover before her box goes dark.
b.recordHint({ name: 'Ada', tMs: 7000, kind: 'dom-active' });
b.recordHint({ name: 'Ada', tMs: 9000, kind: 'dom-active' });   // heartbeat re-assertion
b.recordHint({ name: 'Ada', tMs: 10800, kind: 'dom-active', isEnd: true }); // lingers into handover
// Ben takes over and holds the floor across the commit.
b.recordHint({ name: 'Ben', tMs: 10000, kind: 'dom-active' });
b.recordHint({ name: 'Ben', tMs: 11000, kind: 'dom-active', isEnd: true });

// The diarizer cuts a short (500ms) turn that Ben covers in-span, right after the
// handover. Ada's lingering slice overlaps the ±slack but almost none of the span.
const r = b.resolve({ clusterId: 'seg_42', tStartMs: 10300, tEndMs: 10800 });

console.log(`handover commit → speaker=${r.speakerName} source=${r.source} conf=${r.confidence?.toFixed(3)}`);
const ok = r.speakerName === 'Ben' && r.source === 'window-match';
console.log(ok
  ? '✅ PASS — in-span confidence bound the handover to Ben (not a provisional seg_N)'
  : `❌ FAIL — the ±slack neighbour vote diluted Ben below the confidence gate; turn went provisional (${r.speakerName})`);
process.exit(ok ? 0 : 1);
