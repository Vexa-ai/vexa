/**
 * A transcript timestamp must be WALL time, even when the audio behind it has holes.
 *
 * `cut(t0,t1)` concatenates only the frames that exist, so a span containing a hole yields audio
 * SHORTER than the span it claims to cover. Whisper then reports its segment times relative to that
 * compressed audio, and mapping them as `spanStart + ws.start*1000` silently treats compressed time
 * as wall time: every word after a hole is stamped early by the accumulated hole, and the error
 * accumulates across a turn.
 *
 * This is not hypothetical and it is not a capture bug — capture always has SOME holes (a dropped
 * buffer, a renegotiation, a gated silence). On the corpus entry `jitsi/2026-07-20-capture-gaps`,
 * 108 of 183 recorded segmenter cuts land at a wall time where no audio was ever delivered.
 * Consequences are downstream and expensive: turns open and close against the wrong instants, and
 * the hint binder matches speaker names to a clock that has drifted, so attribution decays too.
 *
 * The fix keeps the audio compressed — sending Whisper a zero-filled hole invites it to hallucinate
 * over the silence and costs STT time for nothing — and instead carries the cut's span layout, so
 * compressed time maps back through the holes to the wall clock it came from.
 *
 *   tsx src/hole-timebase.test.ts
 */
import { ChunkedTranscriber, type BoundarySource } from './chunked-transcriber.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const SR = 16000;
let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
const seg = (text: string, start: number, end: number): any =>
  ({ text, start, end, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1.0 });
const result = (segs: any[]): TranscriptionResult =>
  ({ text: segs.map((s) => s.text).join(' '), language: 'en', language_probability: 0.99, segments: segs } as any);

// The session under test: speech, a 3s hole where no frame ever arrived, then speech again.
//   wall 0–2000ms   audio   ─┐
//   wall 2000–5000ms HOLE    │ cut(0,7000) returns 4s of audio for a 7s span
//   wall 5000–7000ms audio  ─┘
const HOLE_START_MS = 2000, HOLE_END_MS = 5000, SPAN_END_MS = 7000;

async function main(): Promise<void> {
  const published: Array<{ text: string; startMs: number; endMs: number }> = [];
  let emit!: (ev: BoundaryEvent) => void;
  let submittedSamples = 0;

  // Whisper sees 4 seconds of audio and describes it in ITS OWN timebase: the second sentence
  // begins at 2s into the audio it was handed — which is wall 5000ms, not wall 2000ms.
  const SCRIPT = [
    result([seg('before the hole', 0, 2), seg('after the hole', 2, 4)]),
    result([seg('before the hole', 0, 2), seg('after the hole', 2, 4)]),
    result([seg('before the hole', 0, 2), seg('after the hole', 2, 4)]),
  ];
  let i = 0;

  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (pcm: Float32Array) => { submittedSamples = pcm.length; return SCRIPT[Math.min(i++, SCRIPT.length - 1)]; },
    publish: (_s, confirmed) => { for (const c of confirmed) published.push({ text: c.text, startMs: c.startMs, endMs: c.endMs }); },
    publishPending: () => { /* drafts are not the oracle here */ },
    clearPending: () => { /* */ },
    rename: () => { /* */ },
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => { /* */ }, reset() { /* */ } }); },
    log: () => { /* */ },
  });

  emit({ kind: 'silence→speaker', tMs: 0, confidence: 0.9 });
  // Two 1s frames before the hole…
  for (let k = 0; k < 2; k++) { const a = new Float32Array(SR); a.fill(0.2); tc.feedAudio(a, k * 1000); await sleep(400); }
  // …nothing at all across the hole — this is a hole, not silence: no frame ever arrived…
  await sleep(400);
  // …then two more 1s frames after it.
  for (let k = 0; k < 2; k++) { const a = new Float32Array(SR); a.fill(0.2); tc.feedAudio(a, HOLE_END_MS + k * 1000); await sleep(400); }
  emit({ kind: 'speaker→silence', tMs: SPAN_END_MS, confidence: 0.9 });
  await tc.dispose();

  console.log(`  submitted ${(submittedSamples / SR).toFixed(1)}s of audio for a ${(SPAN_END_MS / 1000).toFixed(1)}s span`);
  for (const p of published) console.log(`    [${p.startMs}-${p.endMs}] ${JSON.stringify(p.text)}`);

  ok(published.length > 0, 'the turn published something');
  ok(submittedSamples <= SR * 4.5,
    'the hole is NOT zero-filled — Whisper still receives only the audio that exists');

  const after = published.find((p) => p.text.includes('after the hole'));
  ok(!!after, 'the sentence after the hole was published');

  // THE invariant: no published segment may start inside a window where no audio ever arrived.
  for (const p of published) {
    ok(!(p.startMs >= HOLE_START_MS && p.startMs < HOLE_END_MS),
      `${JSON.stringify(p.text)} starts at ${p.startMs}ms — outside the hole ${HOLE_START_MS}-${HOLE_END_MS}ms`);
  }

  // …and specifically, the post-hole sentence lands after the hole, not shifted back into it.
  ok(after!.startMs >= HOLE_END_MS - 250,
    `the post-hole sentence starts at ${after!.startMs}ms, at or after the hole ends (${HOLE_END_MS}ms)`);

  // The mirror invariant: the sentence BEFORE the hole must not be stretched across it either.
  // Both edges land on the same run boundary and they resolve to opposite sides of it.
  const before = published.find((p) => p.text.includes('before the hole'));
  ok(!!before && before.endMs <= HOLE_START_MS + 250,
    `the pre-hole sentence ends at ${before?.endMs}ms, at or before the hole opens (${HOLE_START_MS}ms)`);

  console.log(`\n✅ hole-timebase: ${checks} checks passed — compressed audio time maps back to the wall clock it came from.`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
