/**
 * never-lose-tail — regression guard for the retract feature deleting live speech.
 *
 * A turn that CONFIRMS early segments (reaching seq>0) and then closes with a still-pending
 * draft tail — via a closing pass that yields nothing (empty/gated STT result) — must PROMOTE
 * that tail to confirmed, never retract it with no replacement. Before the fix, the "never lose
 * a turn" promotion only covered a never-confirmed turn (seq===0), so a confirmed-then-dangling
 * turn silently lost its trailing words (deleted from the live view AND the durable store).
 *
 *   tsx src/never-lose-tail.test.ts
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

const seg = (text: string, start: number, end: number): any =>
  ({ text, start, end, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1.0 });
const result = (segs: any[]): TranscriptionResult =>
  ({ text: segs.map((s) => s.text).join(' '), language: 'en', language_probability: 0.99, segments: segs } as any);

const confirmed: { id: string; text: string }[] = [];
let emit!: (ev: BoundaryEvent) => void;
let i = 0;
// submit 1-3 build "alpha" (confirms via LocalAgreement-3) + a forming "beta ..." tail (pending);
// the close pass yields NOTHING (empty segments) — so without promotion, "beta" is retracted + lost.
const script: TranscriptionResult[] = [
  result([seg('alpha', 0, 1.0)]),
  result([seg('alpha', 0, 1.0), seg('beta', 1.0, 1.6)]),
  result([seg('alpha', 0, 1.0), seg('beta forming', 1.0, 2.0)]),
  result([]), // close → yields nothing → closeOut with a dangling pending tail
];

const tc = await ChunkedTranscriber.create({
  language: 'en',
  transcribe: async () => { const r = script[Math.min(i, script.length - 1)]; i++; return r; },
  publish: (_s, segs) => { for (const c of segs) confirmed.push({ id: c.segmentId, text: c.text }); },
  publishPending: () => {},
  clearPending: () => {},
  rename: () => {},
  makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => {}, reset() {} }); },
  log: () => {},
});

const feed = (fromSec: number, durSec: number): void => {
  const a = new Float32Array(Math.round(SR * durSec)); a.fill(0.1); tc.feedAudio(a, fromSec * 1000);
};
emit({ kind: 'silence→speaker', tMs: 0, confidence: 0.9 });
feed(0, 3); await sleep(1200);   // submit 1
feed(3, 3); await sleep(1200);   // submit 2
feed(6, 3); await sleep(1200);   // submit 3 → confirm "alpha", tail "beta forming" pending
emit({ kind: 'speaker→silence', tMs: 9000, confidence: 0.9 });   // close → yields-nothing pass
await sleep(150);
await tc.dispose();

const texts = confirmed.map((c) => c.text);
const ids = confirmed.map((c) => c.id);
check('early segment confirmed (turn reached seq>0)', texts.includes('alpha'), JSON.stringify(texts));
check('dangling pending tail is PROMOTED on close, not lost', texts.some((t) => t.includes('beta')), JSON.stringify(texts));
check('promoted id continues the seq (no collision with the confirmed segment)',
  new Set(ids).size === ids.length && ids.every((id) => /^turn:\d+:\d+$/.test(id)), JSON.stringify(ids));

if (failed) { console.error(`\n❌ never-lose-tail: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ never-lose-tail: a confirmed-then-dangling turn promotes its tail instead of deleting it.');
