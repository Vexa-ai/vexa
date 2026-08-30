/**
 * text-only-stt.smoke — an OpenAI-compatible endpoint may return only text.
 * The mixed lane assigns that text to the submitted speech window so the
 * minimum custom-STT response remains publishable without provider timestamps.
 */
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

const SAMPLE_RATE = 16_000;
const BASE_MS = 10_000;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
let emit: (event: BoundaryEvent) => void = () => {};
const published: Array<{ text: string; startMs: number; endMs: number }> = [];

const transcriber = await ChunkedTranscriber.create({
  language: 'en',
  transcribe: async () => ({
    text: 'text-only custom STT response',
    language: 'en',
    duration: 2,
    segments: [],
  }),
  publish: (_speaker, confirmed) => { published.push(...confirmed); },
  publishPending: () => {},
  clearPending: () => {},
  rename: () => {},
  makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
    emit = onBoundary;
    return { appendFrame: async () => {}, reset: () => {} };
  },
});

emit({ tMs: BASE_MS, kind: 'silence→speaker', confidence: 0.9 });
await sleep(25);

const halfSecond = new Float32Array(SAMPLE_RATE / 2).fill(0.1);
for (let t = BASE_MS; t < BASE_MS + 2_000; t += 500) transcriber.feedAudio(halfSecond, t);
emit({ tMs: BASE_MS + 2_000, kind: 'speaker→silence', confidence: 0.9 });
await sleep(150);
await transcriber.dispose();

const segment = published.find((item) => item.text === 'text-only custom STT response');
const hasSpeechSpan = Boolean(segment && segment.endMs > segment.startMs);

console.log(`published=${JSON.stringify(published)}`);
if (!hasSpeechSpan) {
  console.error('❌ text-only STT response was not published with a positive speech span');
  process.exit(1);
}

console.log('✅ text-only STT response spans the submitted speech window');
