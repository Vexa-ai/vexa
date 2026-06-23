/**
 * transcript-store.test (L2) — the store UPSERTS confirmed segments by segment_id.
 *
 * Regression: a short mixed-lane turn finalizes provisionally → published with an
 * empty speaker → a later late-box claim re-publishes the SAME segment_id with the
 * real name (via the `rename` callback). The store must REPLACE the provisional copy
 * in place, not accumulate a duplicate — otherwise GET /transcripts returns the stale
 * empty-speaker "ghost" alongside the named segment (the dropped-attribution bug).
 */
import { upsertSegment } from './desktop.js';
import type { TranscriptSegment } from '@vexa/gmeet-pipeline';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const seg = (id: string, speaker: string, text: string, start = 0): TranscriptSegment => ({
  segment_id: id, speaker, text, start, end: start + 1, language: 'en', completed: true,
});

const store: TranscriptSegment[] = [];

// Provisional publish: empty speaker (showSp maps seg_N → '').
upsertSegment(store, seg('turn:4:0', '', "That's right.", 3.6));
check('provisional segment is stored', store.length === 1 && store[0].speaker === '');

// Late-box claim re-publishes the SAME id with the real name → replace in place.
upsertSegment(store, seg('turn:4:0', '❤️Shondis❤️', "That's right.", 3.6));
check('rename REPLACES, no duplicate', store.length === 1, `len=${store.length}`);
check('renamed segment carries the real speaker', store[0].speaker === '❤️Shondis❤️');

// A different id appends.
upsertSegment(store, seg('turn:5:0', 'Easy does it Frank R', 'Bye.', 4.7));
check('new id appends', store.length === 2);

// Order is preserved on replace (the replaced id keeps its original position).
upsertSegment(store, seg('turn:4:0', '❤️Shondis❤️', "That's right (edited).", 3.6));
check('replace preserves position', store[0].segment_id === 'turn:4:0' && store[1].segment_id === 'turn:5:0');
check('replace updates content in place', store[0].text === "That's right (edited).");

// No empty-speaker ghost survives for an id that was later named.
const ids = store.map((s) => s.segment_id);
const dupIds = ids.filter((id, i) => ids.indexOf(id) !== i);
check('no duplicate segment_ids', dupIds.length === 0, JSON.stringify(dupIds));
check('no empty-speaker ghost for a named id', !store.some((s) => !s.speaker), JSON.stringify(store.map((s) => s.speaker)));

if (failed) {
  console.error(`\n❌ transcript-store: ${failed} check(s) FAILED.`);
  process.exit(1);
}
console.log('\n✅ transcript-store: confirmed segments upsert by id; late renames replace the provisional copy (no ghost duplicate).');
