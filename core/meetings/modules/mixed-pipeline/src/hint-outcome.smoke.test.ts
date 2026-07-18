/**
 * hint-outcome.smoke — does the onHintOutcome instrument tell the truth?
 * A hint that names the open turn reports 'matched'; a hint with no overlapping
 * turn reports 'missed' (and is still recorded for later window matches).
 * Injected segmenter + stub Whisper. No network, no model.
 */
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

let emit!: (ev: BoundaryEvent) => void;
const outcomes: { name: string; kind: string; outcome: string }[] = [];

async function main() {
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => ({
      text: 'hello world this is a test',
      language: 'en',
      language_probability: 0.99,
      segments: [{ text: 'hello world this is a test', start: 0, end: 2, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
    }),
    publish: () => {}, publishPending: () => {}, clearPending: () => {}, rename: () => {},
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
      emit = onBoundary;
      return { appendFrame: async () => {}, reset: () => {} };
    },
    onHintOutcome: (o) => outcomes.push({ name: o.name, kind: o.kind, outcome: o.outcome }),
    log: () => {},
  });

  // MISSED: a hint before any audio/turn exists — nothing can overlap it.
  tc.recordHint('Nobody Yet', 'dom-outline', 500);

  // 3s of audio; a turn opens and confirms; Alice's hint lands mid-turn → MATCHED.
  const frame = new Float32Array(1600).fill(0.05);
  for (let t = 60000; t < 63000; t += 100) tc.feedAudio(frame, t);
  emit({ tMs: 60000, kind: 'silence→speaker', confidence: 0.9 });
  await new Promise(r => setTimeout(r, 50));
  emit({ tMs: 63000, kind: 'speaker→silence', confidence: 0.9 });
  await new Promise(r => setTimeout(r, 100));
  tc.recordHint('Alice', 'dom-outline', 61000);

  // An end-hint emits NO outcome (it closes a window, it doesn't bind).
  tc.recordHint('Alice', 'dom-outline', 63200, true);

  await tc.dispose();

  console.log(`outcomes = ${JSON.stringify(outcomes)}`);
  const missedFirst = outcomes[0]?.name === 'Nobody Yet' && outcomes[0]?.outcome === 'missed';
  const aliceMatched = outcomes.some(o => o.name === 'Alice' && o.outcome === 'matched');
  const endSilent = outcomes.length === 2;
  const ok = missedFirst && aliceMatched && endSilent;
  console.log(ok
    ? '✅ PASS — matched/missed report the hint hop truthfully; end-hints stay silent'
    : `❌ FAIL — missedFirst=${missedFirst} aliceMatched=${aliceMatched} endSilent=${endSilent}`);
  process.exit(ok ? 0 : 1);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
