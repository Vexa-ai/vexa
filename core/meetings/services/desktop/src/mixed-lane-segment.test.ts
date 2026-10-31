/**
 * mixed-lane-segment.test (L2) — the mix and the mic lane never share a segment_id.
 *
 * On a mixed platform (Teams/Zoom/YouTube) the desktop runs one ChunkedTranscriber for the
 * remote mix (ch999) and another for the local mic (ch1000). Both emit ChunkSegment ids from
 * the same `turn:<n>:<k>` sequence. The store upserts by segment_id, so equal ids from the two
 * lanes must stay distinct: a mic turn may never replace a remote speaker's confirmed turn.
 */
import { mixedLaneSegment, upsertSegment } from './desktop.js';
import type { TranscriptSegment } from '@vexa/gmeet-pipeline';
import type { ChunkSegment } from '@vexa/mixed-pipeline';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const chunk = (segmentId: string, text: string, startMs: number): ChunkSegment => ({
  segmentId, text, startMs, endMs: startMs + 1500, language: 'en',
});

const store: TranscriptSegment[] = [];

upsertSegment(store, mixedLaneSegment(chunk('turn:0:0', 'Buenos días a todos.', 0), 'Mónica', true));
upsertSegment(store, mixedLaneSegment(chunk('turn:1:0', 'From the bank side, the transfer is ready.', 9000), 'Bank', true));
upsertSegment(store, mixedLaneSegment(chunk('turn:0:0', 'Okay, Monday works for me.', 20000), 'You', true, 'mic'));
upsertSegment(store, mixedLaneSegment(chunk('turn:1:0', 'Please send the draft by Friday.', 23000), 'You', true, 'mic'));

check('all four confirmed turns are stored', store.length === 4, `len=${store.length}`);
check('remote turns survive the mic lane', store.filter((s) => s.speaker !== 'You').length === 2, JSON.stringify(store.map((s) => s.speaker)));
check('mix ids keep the transcriber id', store.some((s) => s.segment_id === 'turn:0:0' && s.speaker === 'Mónica'));
check('mic ids carry the mic lane prefix', store.filter((s) => s.speaker === 'You').every((s) => s.segment_id.startsWith('mic:')), JSON.stringify(store.map((s) => s.segment_id)));

// A rename inside one lane still replaces in place.
upsertSegment(store, mixedLaneSegment(chunk('turn:1:0', 'From the bank side, the transfer is ready.', 9000), 'Notary office', true));
check('same-lane rename replaces, no duplicate', store.length === 4, `len=${store.length}`);
check('renamed remote turn carries the new speaker', store[1].speaker === 'Notary office');

const micTurn = store.find((s) => s.text.startsWith('Okay'));
check('time base is seconds', micTurn?.start === 20 && micTurn?.end === 21.5, JSON.stringify(micTurn));

if (failed) {
  console.error(`\n❌ mixed-lane-segment: ${failed} check(s) FAILED.`);
  process.exit(1);
}
console.log('\n✅ mixed-lane-segment: mix and mic lanes keep distinct segment_ids in the shared store.');
