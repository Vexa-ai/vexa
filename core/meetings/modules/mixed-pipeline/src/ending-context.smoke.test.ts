/**
 * ending-context.smoke — clean speech-end boundaries send a small trailing
 * context pad to STT, but published segment timestamps stay clipped to the
 * committed speech boundary.
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
const submittedSamples: number[] = [];
const published: Array<{ startMs: number; endMs: number; text: string }> = [];

const tc = await ChunkedTranscriber.create({
  transcribe: async (pcm) => {
    submittedSamples.push(pcm.length);
    return {
      text: 'final word',
      language: 'en',
      language_probability: 0.99,
      segments: [{ text: 'final word', start: 0, end: 2.3, no_speech_prob: 0.01, avg_logprob: -0.1, compression_ratio: 1.0 } as any],
    };
  },
  publish: (_speaker, confirmed) => { published.push(...confirmed); },
  publishPending: () => {},
  clearPending: () => {},
  rename: () => {},
  makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
    emit = onBoundary;
    return { appendFrame: async () => {}, reset() {} };
  },
});

emit({ tMs: 0, kind: 'silence→speaker', confidence: 0.9 });
await sleep(25);

const halfSecond = new Float32Array(SAMPLE_RATE / 2).fill(0.1);
for (let t = 0; t <= 2000; t += 500) tc.feedAudio(halfSecond, t);
emit({ tMs: 2000, kind: 'speaker→silence', confidence: 0.9 });
await sleep(150);
await tc.dispose();

const maxSubmittedMs = Math.max(...submittedSamples, 0) / SAMPLE_RATE * 1000;
const lastEnd = Math.max(...published.map((p) => p.endMs), 0);

check('closing submission includes trailing STT context',
  maxSubmittedMs >= 2300 && maxSubmittedMs < 2450,
  `${maxSubmittedMs.toFixed(0)}ms`);
check('published timestamps are clipped to the speech boundary',
  lastEnd <= 2000,
  `${lastEnd}ms`);
check('closing context still publishes the final text',
  published.some((p) => p.text === 'final word'),
  JSON.stringify(published));

if (failed) {
  console.error(`\n❌ ending-context: ${failed} check(s) FAILED.`);
  process.exit(1);
}
console.log('\n✅ ending-context: speech-end STT context preserves finals without moving transcript boundaries.');
