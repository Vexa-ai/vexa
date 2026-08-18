import assert from 'node:assert/strict';
import {
  buildTimelineModel,
  flagContestedWords,
  WORD_CONTEST_KIND,
} from './teams-csrc-timeline.mjs';

const result = {
  kind: 'teams-csrc-gmeet-window-fixture-eval',
  slice: { startSec: 60, durationSec: 20, cutStartMs: 100_000, cutEndMs: 120_000 },
  candidate: {
    spans: [{ csrc: 201, startMs: 101_000, endMs: 104_000 }],
    acceptedSpans: [{ csrc: 201, startMs: 100_500, endMs: 104_250 }],
    confirmed: [
      { csrc: 201, segmentId: 'row-1', text: 'prefix shared words suffix', startMs: 101_500, endMs: 103_500 },
      { csrc: 840, segmentId: 'row-2', text: 'shared words', startMs: 101_700, endMs: 103_000 },
    ],
    pending: [{ csrc: 840, segmentId: 'draft-1', text: 'forming', startMs: 105_000, endMs: 105_000 }],
    health: { tracks: 2 },
    refreshLatency: { cadencePlusDecoderMaxMs: 3900 },
  },
  singlePass: { segments: [{ text: 'reference', start: 1.25, end: 3.75 }] },
};
const annotations = {
  kind: WORD_CONTEST_KIND,
  rows: [{ segmentId: 'row-1', rivalSegmentId: 'row-2', rivalCsrc: 840, contestedText: 'shared words' }],
};
const model = buildTimelineModel(result, annotations);
assert.equal(model.durationSec, 20);
assert.equal(model.cutStartSec, 60);
assert.deepEqual(model.tracks, [201, 840]);
assert.deepEqual(model.spans[0], { csrc: 201, startSec: 1, endSec: 4 });
assert.equal(model.acceptedSpans[0].startSec, 0.5);
assert.equal(model.confirmed[0].startSec, 1.5);
assert.equal(model.singlePass[0].startSec, 1.25);
assert.equal(model.contests.length, 1);
assert.deepEqual(model.displayedConfirmed.map((row) => row.segmentId), ['row-1', 'row-2']);
assert.equal('resolution' in model.contests[0], false);
assert.equal('removedConfirmed' in model, false);
assert.equal(
  flagContestedWords(model.confirmed[0], model.annotations.get('row-1')),
  'prefix [shared words] suffix',
);
assert.equal(flagContestedWords({ text: 'unchanged' }), 'unchanged');
assert.throws(() => buildTimelineModel({ kind: 'wrong' }), /expected teams-csrc/);
assert.throws(() => buildTimelineModel(result, { kind: 'wrong', rows: [] }), /expected teams-csrc-word/);

console.log('teams-csrc-timeline: model and exact-word annotation checks passed');
