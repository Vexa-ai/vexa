/**
 * flicker.smoke — sticky attribution. Once a turn is attributed to a real speaker,
 * a brief flicker hint (the "hmm" box-flash to another speaker) must NOT flip its
 * pending. Priority/(re)attribution is for the UNATTRIBUTED; an attributed turn is
 * locked. (The bug: the open turn re-resolved on every hint, so a momentary flash
 * stole the live speaker, then flipped back.)
 */
import { ChunkedTranscriber, type BoundarySource } from './index';
import type { BoundaryEvent } from './pyannote-segmenter';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let emit!: (ev: BoundaryEvent) => void;
const pubs: string[] = [];
const renames: { old: string; next: string }[] = [];

async function main() {
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => ({
      text: 'this is anna speaking now about the plan today', language: 'en', language_probability: 0.99,
      segments: [{ text: 'this is anna speaking now about the plan today', start: 0, end: 2, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
    }),
    publish: (sp) => { pubs.push(sp); },
    publishPending: (sp) => { pubs.push(sp); },
    clearPending: () => {}, rename: (old, next) => { renames.push({ old, next }); },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => { emit = onBoundary; return { appendFrame: async () => {}, reset: () => {} }; },
    log: () => {},
  });

  const frame = new Float32Array(1600).fill(0.05);
  for (let t = 1000; t < 6000; t += 100) tc.feedAudio(frame, t);
  tc.recordHint('Anna', 'dom-active', 1200);                 // Anna lit → turn attributes to Anna
  emit({ tMs: 1000, kind: 'silence→speaker', confidence: 0.9 });
  await sleep(1800);                                         // tick → submit → attribute + publish under Anna
  tc.recordHint('Bob', 'dom-active', 3000);                  // brief "hmm" flicker to Bob, mid-Anna-turn
  await sleep(1800);                                         // more ticks — must NOT flip to Bob
  emit({ tMs: 6000, kind: 'speaker→silence', confidence: 0.9 });
  await tc.dispose();

  const speakers = [...new Set([...pubs, ...renames.flatMap((r) => [r.old, r.next])])];
  console.log(`speakers seen: ${JSON.stringify(speakers)}  renames: ${JSON.stringify(renames)}`);
  const ok = speakers.includes('Anna') && !speakers.includes('Bob');
  console.log(ok
    ? '✅ PASS — attributed turn stayed Anna; the Bob flicker did not flip pending'
    : '❌ FAIL — a brief flicker flipped the attributed speaker');
  process.exit(ok ? 0 : 1);
}
main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
