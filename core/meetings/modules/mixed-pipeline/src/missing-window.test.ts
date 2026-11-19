/**
 * missing-window — a closing window the STT refused is RECORDED, never silent (prod-cutover R4).
 *
 * R4 (gateway cap 1, slow stub, two bots): the transcription client spent its per-window attempts
 * on 503 "saturated", threw, and the mixed lane closed the turn with nothing — no `[AUDIO-DROPPED]`,
 * no range, no trace in the meeting. The audio was simply gone.
 *
 *   1. a closing window whose transcribe throws is KEPT and sent again later (bounded);
 *   2. if capacity comes back, the retry publishes the words and nothing is missing;
 *   3. if it never does, the window is a missing range [start,end] with the fault as its reason;
 *   4. a window still waiting for its retry at stop is recorded as missing, not dropped.
 *
 *   tsx src/missing-window.test.ts
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
const saturated = (): Error => Object.assign(
  new Error('stt unavailable (HTTP 503): {"detail":"Remote transcription fallback saturated: concurrency"}'),
  { kind: 'unavailable', status: 503, source: 'stt', overloaded: true },
);
const ok = (text: string): TranscriptionResult => ({
  text, language: 'en', language_probability: 0.99,
  segments: [{ text, start: 0, end: 2.5, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1.0 }],
} as any);

const T0 = 1_760_000_000_000;   // epoch ms, as the bot feeds it

async function lane(opts: { healthyAfter?: () => boolean; retries?: number; baseMs?: number }) {
  const confirmed: string[] = [];
  const errors: unknown[] = [];
  let calls = 0;
  let emit!: (ev: BoundaryEvent) => void;
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => {
      calls++;
      if (opts.healthyAfter?.()) return ok('the database is the bottleneck');
      throw saturated();
    },
    publish: (_s, segs) => { for (const c of segs) confirmed.push(c.text); },
    publishPending: () => {},
    clearPending: () => {},
    rename: () => {},
    onError: (e) => errors.push(e),
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => {}, reset() {} }); },
    sttRetryBaseMs: opts.baseMs ?? 100,
    sttRetryMax: opts.retries ?? 2,
    log: () => {},
  } as any);
  const a = new Float32Array(SR * 3); a.fill(0.1);
  emit({ kind: 'silence→speaker', tMs: T0, confidence: 0.9 });
  tc.feedAudio(a, T0);
  emit({ kind: 'speaker→silence', tMs: T0 + 3000, confidence: 0.9 });
  return { tc, confirmed, errors, calls: () => calls };
}

// ── 1+2: capacity returns → the kept window is transcribed, nothing missing ──
{
  let healthy = false;
  const { tc, confirmed, calls } = await lane({ healthyAfter: () => healthy, retries: 3, baseMs: 100 });
  await sleep(300);                      // the closing pass has failed by now
  const before = calls();
  healthy = true;
  await sleep(2500);                     // heartbeat (1 s) picks the due retry up
  const ranges = (tc as any).missingRanges?.() ?? null;
  check('a failed closing window is sent again later', calls() > before, `calls ${before} → ${calls()}`);
  check('the retried window publishes its words', confirmed.some((t) => t.includes('bottleneck')), JSON.stringify(confirmed));
  check('missingRanges() exists and is empty after a successful retry', Array.isArray(ranges) && ranges.length === 0, JSON.stringify(ranges));
  await tc.dispose();
}

// ── 3: capacity never returns → bounded retries, then ONE missing range with the fault ──
{
  const { tc, calls } = await lane({ retries: 2, baseMs: 100 });
  await sleep(4500);
  const ranges = (tc as any).missingRanges?.() ?? [];
  check('retries are bounded (1 + 2 attempts on the window)', calls() === 3, `calls=${calls()}`);
  check('the lost window is recorded as missing before stop', ranges.length === 1, JSON.stringify(ranges));
  const r = ranges[0] ?? {};
  check('range spans the window [start,end] in epoch ms', r.startMs === T0 && r.endMs === T0 + 3000, JSON.stringify(r));
  check('range names the fault', /unavailable|503|saturated/.test(String(r.reason)), String(r.reason));
  await tc.dispose();
  check('dispose does not record it twice', ((tc as any).missingRanges?.() ?? []).length === 1);
}

// ── 4: a window still waiting for its retry at stop is missing, not dropped ──
{
  const { tc } = await lane({ retries: 3, baseMs: 60_000 });
  await sleep(300);
  await tc.dispose();
  const ranges = (tc as any).missingRanges?.() ?? [];
  check('a window pending retry at stop is recorded as missing', ranges.length === 1
    && ranges[0].startMs === T0 && ranges[0].endMs === T0 + 3000, JSON.stringify(ranges));
}

// ── 5: STOP BUDGET — an STT that does not answer at all; the host gives up at its budget ──
// (prod-cutover R4 re-run on bbb: the runtime destroyed a bot still waiting on the STT at stop, so
// its terminal event — and the gap — never left the container.) abandon() records every window
// still owed as missing, at once.
{
  let release!: () => void;
  const hung = new Promise<void>((r) => { release = r; });
  let emit!: (ev: BoundaryEvent) => void;
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => { await hung; throw saturated(); },
    publish: () => {}, publishPending: () => {}, clearPending: () => {}, rename: () => {},
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => {}, reset() {} }); },
    sttRetryBaseMs: 100, sttRetryMax: 2, log: () => {},
  } as any);
  const a = new Float32Array(SR * 3); a.fill(0.1);
  emit({ kind: 'silence→speaker', tMs: T0, confidence: 0.9 });
  tc.feedAudio(a, T0);
  emit({ kind: 'speaker→silence', tMs: T0 + 3000, confidence: 0.9 });
  emit({ kind: 'silence→speaker', tMs: T0 + 3000, confidence: 0.9 });
  tc.feedAudio(a, T0 + 3000);               // a second turn, open, behind the hung closing pass
  await sleep(300);
  const disposing = tc.dispose();
  await sleep(100);
  (tc as any).abandon?.('stt had not transcribed it when the meeting stopped (stop budget 25s)');
  const ranges = (tc as any).missingRanges?.() ?? [];
  const covered = ranges.reduce((n: number, r: any) => n + (r.endMs - r.startMs), 0);
  check('abandon() records the hung closing window and the audio behind it', ranges.length >= 1 && covered >= 5900
    && Math.min(...ranges.map((r: any) => r.startMs)) === T0 && Math.max(...ranges.map((r: any) => r.endMs)) === T0 + 6000,
    JSON.stringify(ranges));
  check('the reason names the stop budget', ranges.every((r: any) => /stop budget/.test(r.reason)), JSON.stringify(ranges));
  release();
  await disposing.catch(() => {});
  check('a late failure after abandon does not record it twice', ((tc as any).missingRanges?.() ?? []).length === ranges.length);
}

if (failed) { console.error(`\n❌ missing-window: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ missing-window: a refused closing window is retried, and recorded as missing when it cannot be.');
