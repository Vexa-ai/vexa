import assert from 'node:assert/strict';
import { detectContestedWords } from './teams-contested-word-detector.mjs';

const words = (tokens, step = 0.5) => tokens.map((word, index) => ({
  word: ` ${word}`,
  start: index * step,
  end: index * step + 0.4,
}));

const result = {
  slice: { cutStartMs: 0 },
  candidate: {
    spans: [
      { csrc: 201, startMs: 1_000, endMs: 5_000 },
      { csrc: 840, startMs: 1_000, endMs: 5_000 },
    ],
    acceptedSpans: [
      { csrc: 201, startMs: 1_000, endMs: 5_000 },
      { csrc: 840, startMs: 1_000, endMs: 5_000 },
    ],
    confirmed: [
      { csrc: 201, sourceKey: 'csrc-201:1', segmentId: 'short', text: 'prefix shared words here suffix', startMs: 1_000, endMs: 4_000 },
      { csrc: 840, sourceKey: 'csrc-840:1', segmentId: 'long', text: 'context alpha shared words here more context around it', startMs: 1_000, endMs: 5_000 },
    ],
    submissions: [
      {
        sourceKey: 'csrc-201:1', hash: 'short-hash',
        segments: [{ words: words(['prefix', 'shared', 'words', 'here', 'suffix']) }],
      },
      {
        sourceKey: 'csrc-840:1', hash: 'long-hash',
        segments: [{ words: words(['context', 'alpha', 'shared', 'words', 'here', 'more', 'context', 'around', 'it']) }],
      },
    ],
  },
  singlePass: {
    segments: [{ words: words(['context', 'alpha', 'shared', 'words', 'here', 'more', 'context', 'around', 'it']) }],
  },
};

const detected = detectContestedWords(result);
assert.equal(detected.rows.length, 1);
assert.equal(detected.rows[0].segmentId, 'short');
assert.equal(detected.rows[0].rivalSegmentId, 'long');
assert.equal(detected.rows[0].rivalCsrc, 840);
assert.equal(detected.rows[0].contestedText, 'shared words here');
assert.equal(detected.rows[0].evidence.matchedTokens, 3);
assert.equal(detected.rows[0].evidence.sharedRoutedMs, 3000);
assert.ok(detected.rows[0].evidence.medianWordDeltaMs <= 500);
assert.equal(detected.rows[0].evidence.traceCounts.short, 1);
assert.equal(detected.rows[0].evidence.traceCounts.long, 1);
assert.equal('assignment' in detected.rows[0], false);
assert.match(detected.rows[0].reason, /ownership intentionally unresolved/);

const flakyWordTimes = structuredClone(result);
flakyWordTimes.candidate.submissions.push({
  sourceKey: 'csrc-201:1', hash: 'short-flaky-pass',
  segments: [{ words: words(['prefix', 'shared', 'words', 'here', 'suffix'], 0.7) }],
});
const smoothed = detectContestedWords(flakyWordTimes);
assert.equal(smoothed.rows.length, 1);
assert.equal(smoothed.rows[0].evidence.traceCounts.short, 2);
assert.equal(smoothed.rows[0].contestedText, 'shared words here');

const observedBoundaryFlake = structuredClone(result);
observedBoundaryFlake.candidate.confirmed[0].startMs += 1_400;
observedBoundaryFlake.candidate.confirmed[0].endMs += 1_400;
assert.equal(detectContestedWords(observedBoundaryFlake).rows.length, 1);

const shortEmbeddedFragment = structuredClone(result);
shortEmbeddedFragment.candidate.confirmed[0].text = 'It probably...';
shortEmbeddedFragment.candidate.submissions[0].segments = [{ words: words(['It', 'probably']) }];
shortEmbeddedFragment.candidate.confirmed[1].text = 'It probably depends how often you use it because you turn it off';
shortEmbeddedFragment.candidate.submissions[1].segments = [{ words: words(['It', 'probably', 'depends', 'how', 'often', 'you', 'use', 'it', 'because', 'you', 'turn', 'it', 'off']) }];
shortEmbeddedFragment.singlePass.segments = [{ words: words(['It', 'probably', 'depends', 'how', 'often', 'you', 'use', 'it', 'because', 'you', 'turn', 'it', 'off']) }];
const shortDetected = detectContestedWords(shortEmbeddedFragment);
assert.equal(shortDetected.rows.length, 1);
assert.equal(shortDetected.rows[0].contestedText, 'It probably');
assert.equal('assignment' in shortDetected.rows[0], false);

const noWords = structuredClone(result);
noWords.candidate.submissions = [];
assert.equal(detectContestedWords(noWords).rows.length, 0);

const noOverlap = structuredClone(result);
noOverlap.candidate.acceptedSpans[1] = { csrc: 840, startMs: 6_000, endMs: 8_000 };
assert.equal(detectContestedWords(noOverlap).rows.length, 0);

const distant = structuredClone(result);
distant.candidate.confirmed[1].startMs = 5_000;
distant.candidate.confirmed[1].endMs = 8_000;
distant.candidate.acceptedSpans[1] = { csrc: 840, startMs: 3_000, endMs: 8_000 };
assert.equal(detectContestedWords(distant).rows.length, 0);

console.log('teams-contested-word-detector: overlap, word-time, text, and negative controls passed');
