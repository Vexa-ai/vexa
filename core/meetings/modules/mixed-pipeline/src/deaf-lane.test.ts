/**
 * deaf-lane — under UNBROKEN speech the lane must keep transcribing. Two ways it used to stop:
 *
 *   1. The segmenter only emits EDGES. Speech already in progress when the stream starts has no
 *      silence→speaker edge, and an edge swallowed by the re-detection dedup (a pause shorter than
 *      the dedup floor) never comes back — so no turn opens until some later edge, which under
 *      continuous speech (the capture gate drops the silent frames that would carry a pause) can be
 *      minutes away.
 *   2. The TTL idle-finalize closed the open pyannote turn without opening a successor, leaving
 *      the lane with no turn until the next segmenter edge — the same minutes.
 *
 * Both present identically live: frames keep arriving, CPU stays busy, no error, no STT request.
 *
 *   tsx src/deaf-lane.test.ts
 */
import { ChunkedTranscriber, VirtualClock, type BoundarySource } from './index.js';
import { PyannoteSegmenter, type BoundaryEvent } from './pyannote-segmenter.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const SR = 16000;
const FRAME = 4096;
const FRAME_MS = (FRAME / SR) * 1000;
let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const voiced = (n: number): Float32Array => {
  const a = new Float32Array(n);
  for (let i = 0; i < n; i++) a[i] = 0.1 * Math.sin(i / 7);
  return a;
};

// ── 1. the segmenter's edges converge on what the model sees ──────────────────────────────────
// A stand-in model: a frame is speaker 1 when its slice of the window carries energy, else silence.
const FRAMES_PER_WINDOW = 767;
function energyModel(): { processor: unknown; model: unknown } {
  return {
    processor: async (window: Float32Array) => ({ window }),
    model: async ({ window }: { window: Float32Array }) => {
      const data = new Float32Array(FRAMES_PER_WINDOW * 7);
      const per = window.length / FRAMES_PER_WINDOW;
      for (let f = 0; f < FRAMES_PER_WINDOW; f++) {
        let e = 0;
        const from = Math.floor(f * per), to = Math.floor((f + 1) * per);
        for (let i = from; i < to; i++) e += window[i] * window[i];
        const cls = e / Math.max(1, to - from) > 1e-4 ? 1 : 0;
        data[f * 7 + cls] = 5;
      }
      return { logits: { dims: [1, FRAMES_PER_WINDOW, 7], data } };
    },
  };
}
function segmenterWithStandInModel(events: BoundaryEvent[]): PyannoteSegmenter {
  const s = new (PyannoteSegmenter as unknown as new (cfg: object) => PyannoteSegmenter)({
    inferIntervalMs: 500,
    onBoundary: (ev: BoundaryEvent) => events.push(ev),
  });
  Object.assign(s, energyModel());
  return s;
}
async function feedSegmenter(s: PyannoteSegmenter, pcm: Float32Array, t0: number): Promise<void> {
  for (let off = 0; off + FRAME <= pcm.length; off += FRAME) {
    await s.appendFrame(pcm.subarray(off, off + FRAME), t0 + (off / SR) * 1000);
  }
}

console.log('segmenter — speech already in progress at the first frame');
{
  const events: BoundaryEvent[] = [];
  await feedSegmenter(segmenterWithStandInModel(events), voiced(SR * 6), 0);
  check('a silence→speaker edge opens the speech the stream started inside',
    events.some((e) => e.kind === 'silence→speaker'), JSON.stringify(events));
}

console.log('segmenter — a pause shorter than the re-detection floor');
{
  const events: BoundaryEvent[] = [];
  const pcm = voiced(SR * 8);
  pcm.fill(0, SR * 3, SR * 3 + Math.round(SR * 0.08));   // 80 ms: past the despeckle, inside the dedup floor
  await feedSegmenter(segmenterWithStandInModel(events), pcm, 0);
  const last = events[events.length - 1];
  check('the edge stream ends in speech, as the audio does',
    last?.kind === 'silence→speaker' || last?.kind === 'speaker→speaker', JSON.stringify(events));
  check('edges stay in time order', events.every((e, i) => i === 0 || e.tMs > events[i - 1].tMs), JSON.stringify(events));
}

console.log('segmenter — continuous speech emits no further edges once it has opened');
{
  const events: BoundaryEvent[] = [];
  await feedSegmenter(segmenterWithStandInModel(events), voiced(SR * 20), 0);
  check('exactly one edge for one unbroken utterance', events.length === 1, JSON.stringify(events));
}

// ── 2. the TTL finalize leaves a turn open behind it ──────────────────────────────────────────
console.log('lane — TTL finalize under unbroken speech');
{
  process.env.VEXA_TRACE_SPANS = '1';   // the lane's span trace is how the test sees a turn close
  const START = 1_800_000_000_000;
  let tc!: ChunkedTranscriber;
  const clock = new VirtualClock({
    startMs: START,
    drain: async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); },
  });
  const calls: number[] = [];
  const logs: string[] = [];
  let emit!: (ev: BoundaryEvent) => void;
  // Every pass hears new words (live speech), none of them agree yet: the turn always has a
  // pending tail, which is what arms the TTL.
  tc = await ChunkedTranscriber.create({
    language: 'en',
    clock,
    transcribe: async (): Promise<TranscriptionResult> => {
      calls.push(clock.now());
      const text = `word${calls.length}`;
      return { text, language: 'en', duration: 1, segments: [{ start: 0, end: 1, text, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1 }] } as TranscriptionResult;
    },
    publish: () => {}, publishPending: () => {}, clearPending: () => {}, rename: () => {},
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => [], reset() {} }); },
    log: (m) => logs.push(m),
  });
  emit({ kind: 'silence→speaker', tMs: START, confidence: 0.9 });
  const pcm = voiced(FRAME);
  const MINUTES = 3;
  for (let n = 0; n * FRAME_MS < MINUTES * 60_000; n++) {
    const ts = START + n * FRAME_MS;
    await clock.advanceTo(ts + FRAME_MS);
    tc.feedAudio(pcm, ts);
  }
  const rel = calls.map((t) => (t - START) / 1000);
  let longestIdle = 0;
  for (let i = 1; i < rel.length; i++) longestIdle = Math.max(longestIdle, rel[i] - rel[i - 1]);
  longestIdle = Math.max(longestIdle, MINUTES * 60 - (rel[rel.length - 1] ?? 0));
  const firstClose = logs.find((l) => /closing=true/.test(l));
  check('a turn finalized under speech (the scenario is exercised)', firstClose !== undefined, `calls=${rel.length}`);
  check('STT is never idle for more than 10 s of unbroken speech', longestIdle <= 10,
    `longest idle ${longestIdle.toFixed(1)}s across ${rel.length} call(s)`);
  await tc.dispose();
}

if (failed) { console.log(`\n❌ ${failed} check(s) failed`); process.exit(1); }
console.log('\n✅ deaf-lane: unbroken speech keeps reaching STT');
