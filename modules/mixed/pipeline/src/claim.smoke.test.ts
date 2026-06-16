/**
 * claim.smoke — the late-box claim: a turn that finalizes BEFORE any hint publishes
 * provisionally (seg_N); when the speaker's box finally lights (a hint beyond the
 * binder's window-match reach but within CLAIM_WINDOW_MS), the provisional turn is
 * claimed for that speaker and its segments repaint (rename seg_N → name).
 */
import { ChunkedTranscriber, type BoundarySource } from './index';
import type { BoundaryEvent } from './pyannote-segmenter';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let emit!: (ev: BoundaryEvent) => void;
const published: { speaker: string }[] = [];
const renames: { oldS: string; newS: string; n: number }[] = [];

async function main() {
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => ({
      text: 'hello world this is a test', language: 'en', language_probability: 0.99,
      segments: [{ text: 'hello world this is a test', start: 0, end: 2, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
    }),
    publish: (speaker) => { published.push({ speaker }); },
    publishPending: () => {}, clearPending: () => {},
    rename: (oldS, newS, segs) => { renames.push({ oldS, newS, n: segs.length }); },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => { emit = onBoundary; return { appendFrame: async () => {}, reset: () => {} }; },
    log: () => {},
  });

  const frame = new Float32Array(1600).fill(0.05);
  for (let t = 1000; t < 3000; t += 100) tc.feedAudio(frame, t);
  emit({ tMs: 1000, kind: 'silence→speaker', confidence: 0.9 });   // open — NO hint exists yet
  await sleep(40);
  emit({ tMs: 3000, kind: 'speaker→silence', confidence: 0.9 });   // close → publishes provisional seg_N
  await sleep(60);

  const publishedSegN = published.some((p) => /^seg_\d+$/.test(p.speaker));

  // The box lights up 4s after the turn ended — beyond the 2.5s window-match reach,
  // but inside CLAIM_WINDOW_MS (8s) → the provisional turn should be claimed for Bob.
  tc.recordHint('Bob', 'dom-active', 7000);
  await sleep(40);
  await tc.dispose();

  const claimed = renames.some((r) => r.newS === 'Bob' && /^seg_\d+$/.test(r.oldS));
  console.log(`published as seg_N: ${publishedSegN} · claimed → Bob: ${claimed} (renames: ${JSON.stringify(renames)})`);
  const ok = publishedSegN && claimed;
  console.log(ok ? '✅ PASS — late-box claim renamed the provisional turn to the new speaker' : '❌ FAIL');
  process.exit(ok ? 0 : 1);
}
main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
