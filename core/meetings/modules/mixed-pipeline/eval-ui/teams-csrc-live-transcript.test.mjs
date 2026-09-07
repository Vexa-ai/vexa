import assert from 'node:assert/strict';
import { buildContestPlan, flagContestedText, toTranscriptSegment } from './teams-csrc-live-model.mjs';

const cutStartMs = 100_000;
const result = {
  candidate: {
    events: [
      { segmentId: 'left', csrc: 201, text: 'shared words left', startMs: 101_000, endMs: 102_000, emittedAtMs: 103_000, completed: false },
      { segmentId: 'left', csrc: 201, text: 'shared words left', startMs: 101_000, endMs: 102_000, emittedAtMs: 104_000, completed: true },
      { segmentId: 'right', csrc: 840, text: 'shared words right', startMs: 101_100, endMs: 102_100, emittedAtMs: 105_000, completed: true },
    ],
  },
};
result.candidate.acceptedSpans = [
  { csrc: 201, startMs: 100_500, endMs: 103_000 },
  { csrc: 840, startMs: 100_500, endMs: 103_000 },
];
result.candidate.confirmed = [
  { ...result.candidate.events[1], sourceKey: 'left-source' },
  { ...result.candidate.events[2], sourceKey: 'right-source' },
];
result.candidate.submissions = [
  { sourceKey: 'left-source', segments: [{ words: [{ word: ' shared', start: 0, end: .4 }, { word: ' words', start: .5, end: .9 }, { word: ' left', start: 1, end: 1.4 }] }] },
  { sourceKey: 'right-source', segments: [{ words: [{ word: ' shared', start: 0, end: .4 }, { word: ' words', start: .5, end: .9 }, { word: ' right', start: 1, end: 1.4 }] }] },
];

const plan = buildContestPlan(result);
assert.equal(plan.length, 1);
assert.equal(plan[0].decisionAtMs, 105_000, 'contest activates only after both confirmations');
const unresolved = toTranscriptSegment(result.candidate.events[0], cutStartMs, plan[0]);
assert.equal(unresolved.text, '⟦shared words⟧{CSRC 201↔CSRC 840} left');
assert.equal(unresolved.contested, true);
const rival = toTranscriptSegment(result.candidate.events[2], cutStartMs, plan[0]);
assert.equal(rival.text, '⟦shared words⟧{CSRC 840↔CSRC 201} right');
assert.equal(rival.latencyMs, 3900);
assert.equal(rival.start_time, 1.1);
assert.equal(flagContestedText('Correct.', 'Correct', 414, 201), '⟦Correct⟧{CSRC 414↔CSRC 201}.');

console.log('teams-csrc-live-transcript: decision-time, two-party notation, and latency checks passed');
