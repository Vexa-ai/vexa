/**
 * gap-reclaim — regression guard for the segmenter's silence verdict DELETING speech.
 *
 * Only audio inside a turn is ever submitted to STT, so a stretch the segmenter labels
 * silence is never transcribed at all. Measured on clean single-speaker audio with a
 * Deepgram oracle (270s fixture, src/clean-audio-replay.ts): the streaming segmenter
 * declared 131.5s of 270s as speech and 79 of the oracle's 251 words fell outside every
 * declared region — Whisper straight through scored 7.5% WER on the same file, the lane
 * 27.2%, and the gap was words never sent.
 *
 * The lane now submits the inter-turn gap WITH the next turn when it carries energy, and
 * still never sends genuine silence (Whisper hallucinates on it). This test pins both
 * halves, plus the invariant that the ATTRIBUTION window is unchanged — reclaim widens
 * what STT hears, never what the namer resolves over.
 *
 *   tsx src/gap-reclaim.test.ts
 */
import { ChunkedTranscriber, type BoundarySource } from './chunked-transcriber.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const SR = 16000;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

/** One run: audio 0..9s, a turn over 0..1s, a 5s GAP the segmenter calls silence, then a
 *  turn over 6..9s. `gapLoud` decides whether the gap carries energy. Returns the duration
 *  (seconds) of every window handed to STT, and the turn windows the namer saw. */
async function run(gapLoud: boolean): Promise<{ submitted: number[]; hints: number[] }> {
  const submitted: number[] = [];
  const hints: number[] = [];
  let emit!: (ev: BoundaryEvent) => void;
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (pcm: Float32Array): Promise<TranscriptionResult> => {
      submitted.push(pcm.length / SR);
      return { text: 'x', language: 'en', language_probability: 0.99, duration: pcm.length / SR,
        segments: [{ text: 'x', start: 0, end: pcm.length / SR, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1 } as any] } as any;
    },
    publish: () => {}, publishPending: () => {}, clearPending: () => {}, rename: () => {},
    onHintOutcome: () => {},
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => {}, reset() {} }); },
    log: (m) => { const g = /reclaim turn=\d+ (\d+(?:\.\d+)?)\.\.(\d+(?:\.\d+)?)/.exec(m); if (g) hints.push(Number(g[2]) - Number(g[1])); },
  });

  const feed = (fromSec: number, durSec: number, amp: number): void => {
    const a = new Float32Array(Math.round(SR * durSec)); a.fill(amp); tc.feedAudio(a, fromSec * 1000);
  };
  emit({ kind: 'silence→speaker', tMs: 0, confidence: 0.9 });
  feed(0, 1, 0.1);
  emit({ kind: 'speaker→silence', tMs: 1000, confidence: 0.9 });
  await sleep(200);
  // The stretch the segmenter got wrong (or right, when gapLoud=false).
  feed(1, 5, gapLoud ? 0.1 : 0.0);
  await sleep(200);
  feed(6, 3, 0.1);
  emit({ kind: 'silence→speaker', tMs: 6000, confidence: 0.9 });
  await sleep(200);
  emit({ kind: 'speaker→silence', tMs: 9000, confidence: 0.9 });
  await sleep(200);
  await tc.dispose();
  return { submitted, hints };
}

process.env.VEXA_TRACE_SPANS = '1';   // the reclaim trace is how the test reads the decision

const loud = await run(true);
const quiet = await run(false);

check('speech the segmenter missed IS submitted (a window covers the 5s gap)',
  loud.submitted.some((d) => d >= 5), JSON.stringify(loud.submitted));
check('the reclaim is bounded to the gap, not the whole ring',
  loud.hints.length > 0 && loud.hints.every((d) => d <= 10_000), JSON.stringify(loud.hints));
check('a SILENT gap is never reclaimed (Whisper is not fed silence)',
  quiet.hints.length === 0 && !quiet.submitted.some((d) => d >= 5), JSON.stringify({ hints: quiet.hints, submitted: quiet.submitted }));

if (failed) { console.error(`\n❌ gap-reclaim: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ gap-reclaim: a segmenter false negative no longer deletes speech, and silence is still never submitted.');
