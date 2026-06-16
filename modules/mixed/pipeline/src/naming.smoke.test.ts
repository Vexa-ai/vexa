/**
 * naming.smoke — does @vexa/mixed-pipeline bind a hint name to a segmentation
 * turn? Injected segmenter + stub Whisper + one recordHint. No network, no model.
 * Exists to pin the hints-only namer after the clustering carve.
 */
import { ChunkedTranscriber, type BoundarySource } from './index';
import type { BoundaryEvent } from './pyannote-segmenter';

let emit!: (ev: BoundaryEvent) => void;
const published: { speaker: string; text: string }[] = [];

async function main() {
const tc = await ChunkedTranscriber.create({
  language: 'en',
  transcribe: async () => ({
    text: 'hello world this is a test',
    language: 'en',
    language_probability: 0.99,
    segments: [{ text: 'hello world this is a test', start: 0, end: 2, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
  }),
  publish: (speaker, confirmed) => { for (const c of confirmed) published.push({ speaker, text: c.text }); },
  publishPending: () => {}, clearPending: () => {}, rename: () => {},
  makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
    emit = onBoundary;
    return { appendFrame: async () => {}, reset: () => {} };
  },
  log: () => {},
});

// 3s of audio at ts 1000..4000ms; a hint "Alice" lit at 2000ms (dom-active, lag 250 → 1750).
const frame = new Float32Array(1600).fill(0.05);   // 100ms, above DROP_RMS
for (let t = 1000; t < 4000; t += 100) tc.feedAudio(frame, t);
tc.recordHint('Alice', 'dom-active', 2000);
emit({ tMs: 1000, kind: 'silence→speaker', confidence: 0.9 });
await new Promise(r => setTimeout(r, 50));
emit({ tMs: 4000, kind: 'speaker→silence', confidence: 0.9 });
await tc.dispose();

const names = [...new Set(published.map(p => p.speaker))];
console.log(`published ${published.length} segment(s); speakers = ${JSON.stringify(names)}`);
const ok = published.length > 0 && names.includes('Alice') && !names.some(n => /^seg_\d+$/.test(n));
console.log(ok ? '✅ PASS — hint name bound to the segmentation turn' : '❌ FAIL — turn published under a provisional seg_N (naming broken)');
process.exit(ok ? 0 : 1);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
