/**
 * coverage.smoke — NO AUDIO IS LOST BETWEEN CUTS.
 *
 * The cut source answers "where did the speaker change". It does not get to answer "which audio
 * exists". pyannote's boundary stream has multi-second recall gaps over continuous speech: a
 * `speaker→silence` emitted from an unstable window edge, whose complementary re-open never
 * fires, so the next boundary the lane sees is an overlap edge seconds later. Measured on a
 * 267.7s flat fixture: 19.5s of its 167.5s of speech sat inside such a span, captured, ringed and
 * NEVER submitted — 107 golden words the transcript could not contain at any latency.
 *
 * So this fixes the shape of that failure, not the fixture: close a turn, let the audio keep
 * arriving, and re-open only much later. Every millisecond in between must still reach STT.
 */
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

const SAMPLE_RATE = 16000;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

let emit: (ev: BoundaryEvent) => void = () => {};
/** Every span STT was asked about, in audio time, reconstructed from the samples it received. */
const asked: Array<[number, number]> = [];
const published: Array<{ startMs: number; endMs: number; text: string }> = [];

// The mock names the window it was handed, so a segment that covers audio nobody submitted is
// impossible to fake: the text can only describe samples that were actually passed in.
const tc = await ChunkedTranscriber.create({
  language: 'en',
  transcribe: async (pcm) => {
    const dur = pcm.length / SAMPLE_RATE;
    const text = `speech(${dur.toFixed(2)}s)`;
    return {
      text, language: 'en', language_probability: 0.99, duration: dur,
      segments: [{ text, start: 0, end: dur, no_speech_prob: 0.01, avg_logprob: -0.1, compression_ratio: 1.0 } as any],
    };
  },
  publish: (_speaker, confirmed) => { published.push(...confirmed.filter((c) => c.text)); },
  publishPending: () => { /* drafts are not the oracle here */ },
  clearPending: () => { /* */ },
  rename: () => { /* */ },
  makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
    emit = onBoundary;
    return { appendFrame: async () => { /* */ }, reset() { /* */ } };
  },
});

const frame = new Float32Array(SAMPLE_RATE / 4).fill(0.1);   // 250ms of voiced-level audio
const feedThrough = async (fromMs: number, toMs: number): Promise<void> => {
  for (let t = fromMs; t < toMs; t += 250) { tc.feedAudio(frame, t); await sleep(0); }
};

// Speech starts, runs for 3s, and the segmenter calls it over…
emit({ tMs: 0, kind: 'silence→speaker', confidence: 0.9 });
await feedThrough(0, 3000);
emit({ tMs: 3000, kind: 'speaker→silence', confidence: 0.9 });
await sleep(120);
// …but the room never stopped talking, and the cut source says nothing for five more seconds.
await feedThrough(3000, 8000);
emit({ tMs: 8000, kind: 'overlap-onset', confidence: 0.9 });
await sleep(120);
await feedThrough(8000, 9000);
emit({ tMs: 9000, kind: 'speaker→silence', confidence: 0.9 });
await sleep(200);
await tc.dispose();

// The lane's coverage, as the transcript shows it: which audio instants ended up inside a
// published segment. The gap [3000,8000] is the whole question.
const covers = (fromMs: number, toMs: number): number => {
  let n = 0;
  for (let t = fromMs; t < toMs; t += 100) if (published.some((p) => p.startMs <= t && p.endMs > t)) n++;
  return n / ((toMs - fromMs) / 100);
};
const gapCoverage = covers(3000, 8000);

console.log(`  published ${published.length} segment(s): ${published.map((p) => `[${Math.round(p.startMs)},${Math.round(p.endMs)}]`).join(' ')}`);
check('the audio between a close and a late re-open reaches the transcript',
  gapCoverage >= 0.9, `only ${(gapCoverage * 100).toFixed(0)}% of [3s,8s] is covered by any segment`);
check('speech before the close is still published', covers(500, 2500) >= 0.9, `${(covers(500, 2500) * 100).toFixed(0)}%`);
check('speech after the re-open is still published', covers(8100, 8900) >= 0.9, `${(covers(8100, 8900) * 100).toFixed(0)}%`);
// A back-extended span must never re-publish audio a previous turn already confirmed.
const overlapping = published.filter((p, i) => published.some((q, j) => j < i && q.endMs > p.startMs + 50 && q.startMs < p.endMs - 50));
check('no segment re-publishes audio an earlier one already covered',
  overlapping.length === 0, JSON.stringify(overlapping.map((p) => [Math.round(p.startMs), Math.round(p.endMs)])));

if (failed) {
  console.error(`\n❌ coverage: ${failed} check(s) FAILED.`);
  process.exit(1);
}
console.log('\n✅ coverage: a turn begins where the last one ended — a silent cut source delays words, it never deletes them.');
