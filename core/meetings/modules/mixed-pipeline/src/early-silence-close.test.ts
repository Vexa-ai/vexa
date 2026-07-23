/**
 * A new turn must not be ended by a speaker→silence wobble less than two seconds
 * after the prior accepted cut. This guard is deliberately narrow:
 * speaker→speaker remains a hard cut at any spacing, and a close at exactly two
 * seconds remains valid.
 */
import { ChunkedTranscriber, type BoundarySource } from './chunked-transcriber.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const SR = 16000;
const FRAME_MS = 200;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
let failed = 0;

function check(name: string, condition: boolean, detail = ''): void {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : ` — ${detail}`}`);
  if (!condition) failed++;
}

let emit: (event: BoundaryEvent) => void = () => {};
const logs: string[] = [];
const result = (durationSec: number): TranscriptionResult => ({
  text: 'spoken words',
  language: 'en',
  language_probability: 0.99,
  segments: [{
    text: 'spoken words',
    start: 0,
    end: durationSec,
    no_speech_prob: 0.01,
    avg_logprob: -0.1,
    compression_ratio: 1,
  } as any],
} as TranscriptionResult);

const transcriber = await ChunkedTranscriber.create({
  language: 'en',
  transcribe: async (pcm) => result(pcm.length / SR),
  publish: () => {},
  publishPending: () => {},
  clearPending: () => {},
  rename: () => {},
  makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
    emit = onBoundary;
    return { appendFrame: async () => {}, reset: () => {} };
  },
  log: (message) => logs.push(message),
});

const pcm = new Float32Array((SR * FRAME_MS) / 1000).fill(0.1);
async function feed(fromMs: number, toMs: number): Promise<void> {
  for (let tMs = fromMs; tMs < toMs; tMs += FRAME_MS) {
    transcriber.feedAudio(pcm, tMs);
    await sleep(0);
  }
}
async function settle(): Promise<void> {
  await sleep(60);
}
function closingEnds(): number[] {
  return logs
    .map((line) => line.match(/\[submit\].*span=\[\d+,(\d+)\].*closing=1/))
    .filter((match): match is RegExpMatchArray => match !== null)
    .map((match) => Number(match[1]));
}

emit({ kind: 'silence→speaker', tMs: 0, confidence: 0.9 });
await feed(0, 1000);
emit({ kind: 'speaker→speaker', tMs: 1000, confidence: 0.9 });
await settle();
check('a real speaker change remains a hard cut inside two seconds',
  closingEnds().includes(1000), `closing ends: ${closingEnds().join(', ')}`);

await feed(1000, 1400);
const closesBeforeEarlySilence = closingEnds().length;
emit({ kind: 'speaker→silence', tMs: 1400, confidence: 0.9 });
await settle();
check('speaker→silence 400ms after the prior cut is ignored',
  closingEnds().length === closesBeforeEarlySilence,
  `closing ends: ${closingEnds().join(', ')}`);
check('the ignored close is observable',
  logs.some((line) => line.includes('ignore early speaker→silence')
    && line.includes('400ms after prior cut')));

emit({ kind: 'silence→speaker', tMs: 1700, confidence: 0.9 });
await settle();
check('the complementary onset resumes without cutting the open turn',
  closingEnds().length === closesBeforeEarlySilence,
  `closing ends: ${closingEnds().join(', ')}`);
check('the resumed turn is observable',
  logs.some((line) => line.includes('keep turn open across 300ms silence wobble')));

await feed(1400, 1800);
emit({ kind: 'speaker→speaker', tMs: 1800, confidence: 0.9 });
await settle();
check('a real speaker change is still accepted after an ignored close',
  closingEnds().includes(1800), `closing ends: ${closingEnds().join(', ')}`);

await feed(1800, 2200);
emit({ kind: 'overlap-onset', tMs: 2200, confidence: 0.9 });
await settle();
check('overlap onset remains a hard cut inside two seconds',
  closingEnds().includes(2200), `closing ends: ${closingEnds().join(', ')}`);

await feed(2200, 2600);
emit({ kind: 'overlap-offset', tMs: 2600, confidence: 0.9 });
await settle();
check('overlap offset remains a hard cut inside two seconds',
  closingEnds().includes(2600), `closing ends: ${closingEnds().join(', ')}`);

await feed(2600, 3000);
const closesBeforeOverlapClose = closingEnds().length;
emit({ kind: 'speaker→silence', tMs: 3000, confidence: 0.9 });
await settle();
check('speaker→silence after an overlap cut gets the same narrow guard',
  closingEnds().length === closesBeforeOverlapClose,
  `closing ends: ${closingEnds().join(', ')}`);

await feed(3000, 4600);
emit({ kind: 'speaker→silence', tMs: 4600, confidence: 0.9 });
await settle();
check('an ignored close does not move the clock: exactly two seconds still closes',
  closingEnds().includes(4600), `closing ends: ${closingEnds().join(', ')}`);

emit({ kind: 'silence→speaker', tMs: 4600, confidence: 0.9 });
await feed(4600, 6600);
emit({ kind: 'speaker→silence', tMs: 6600, confidence: 0.9 });
await settle();
check('a later close at exactly two seconds still closes',
  closingEnds().includes(6600), `closing ends: ${closingEnds().join(', ')}`);

await transcriber.dispose();

if (failed) {
  console.error(`\n❌ early silence close: ${failed} check(s) failed`);
  process.exit(1);
}
console.log('\n✅ early silence close: narrow two-second guard passed');
