/**
 * Regression — one slow STT request never holds up the rest of a speaker's audio (app.dev voice
 * note, 2026-10-13).
 *
 * The person spoke continuously; one request to the remote STT did not answer for minutes while the
 * service's /health answered in ~1 s. The lane sent nothing else for that speaker until it did:
 *   [COVERAGE] captured=62.9s  submitted=58.5s transcribed=0s
 *   [COVERAGE] captured=182.9s submitted=58.5s transcribed=0s
 * and the page read "no words for 2m 38s" while the person talked.
 *
 * Here an STT stub hangs exactly one request for far longer than the test feeds audio; every other
 * request answers in 30 ms. The lane must keep transcribing past the hung request, publish every
 * counted number once and in order, and say how far behind it is while it is.
 *
 *   tsx src/stt-hol.test.ts
 */
import { createGmeetPipeline, type TranscriptSegment, type TranscriptSink } from './index.js';
import { setLogger } from './log.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}
const logs: string[] = [];
setLogger((m) => { logs.push(String(m)); if (process.env.DBG) console.log("LOG", m); });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const SR = 16000;
const NUM = 4800;                                           // 0.3 s per counted number
// Amplitudes offset by 10 so every number is well above the silence gate.
const pcmFor = (k: number) => new Float32Array(NUM).fill((k + 10) / 1000);
const decode = (pcm: Float32Array): TranscriptionResult => {
  const nums: { k: number; start: number; end: number }[] = [];
  let i = 0;
  while (i < pcm.length) {
    const v = Math.round(pcm[i] * 1000);
    if (v === 0) { i++; continue; }
    const s = i;
    while (i < pcm.length && Math.round(pcm[i] * 1000) === v) i++;
    nums.push({ k: v - 10, start: s / SR, end: i / SR });
  }
  return {
    text: nums.map((n) => `alpha${n.k} bravo${n.k * 7} charlie${n.k * 13}`).join(' '), language: 'en', language_probability: 0.99,
    duration: pcm.length / SR, segments: nums.map((n) => ({ start: n.start, end: n.end, text: `alpha${n.k} bravo${n.k * 7} charlie${n.k * 13}` })),
  };
};
const numsIn = (t: string) => (t.match(/alpha(\d+)/g) || []).map((w) => Number(w.slice(5)));

// ── A: continuous speech on one channel; request #2 hangs for 8 s ────────────────────────────────
{
  const HANG_MS = 8000;
  let requests = 0;
  let hungAnswered = false;
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
    const n = ++requests;
    if (n === 2) { await sleep(HANG_MS); hungAnswered = true; return decode(pcm); }
    await sleep(30);
    return decode(pcm);
  };
  const confirmed: TranscriptSegment[] = [];
  const progress: { backlog_s: number; silence_s: number | null }[] = [];
  const sink: TranscriptSink = { segment: (s) => confirmed.push(s), draft: () => {}, finalize: () => {} };
  const pipe = createGmeetPipeline({
    transcribe, sink,
    config: { submitInterval: 0.1, minAudioDuration: 0.6, sttStallMinMs: 400, sttStallFactor: 1 },
    coverageLogIntervalMs: 0,
    onProgress: (p) => progress.push(p),
    progressIntervalMs: 200,
  });
  // 40 numbers = 12 s of speech, fed over 4 s of wall clock, one channel, no gaps (a voice note).
  let ts = 1_800_000_000_000;
  const TOTAL = 40;
  for (let k = 1; k <= TOTAL; k++) { pipe.feedAudio(0, 'Me', pcmFor(k), ts); ts += 300; await sleep(100); }
  const whileSpeaking = progress.slice();
  await sleep(800);
  ok(!hungAnswered, 'the hung request is still unanswered when the check runs');
  const soFar = confirmed.flatMap((s) => numsIn(s.text));
  ok(soFar.length >= 25, `words keep arriving past the hung request (${soFar.length}/${TOTAL} confirmed while it hangs)`);
  ok(logs.some((l) => l.includes('[STT-STALLED]')), 'the stalled request is logged once it outlives its stall threshold');
  const behind = progress.filter((p) => p.backlog_s > 0).length;
  ok(progress.length > 0 && behind > 0, `the lane reports a backlog while it is behind (${progress.length} reports, ${behind} with backlog)`);
  ok(whileSpeaking.length > 0 && whileSpeaking.every((p) => p.silence_s !== null && p.silence_s < 1),
    `a speaking person is never reported as silent (${JSON.stringify(whileSpeaking.map((p) => p.silence_s))})`);
  await pipe.dispose();
  ok(hungAnswered, 'dispose waited for the hung request instead of abandoning it');
  const all = confirmed.flatMap((s) => numsIn(s.text));
  const missing: number[] = [];
  for (let i = 1; i <= TOTAL; i++) if (!all.includes(i)) missing.push(i);
  const dupes = all.filter((n, i) => all.indexOf(n) !== i);
  ok(missing.length === 0, `every counted number reaches the transcript (missing: ${missing.join(',')})`);
  ok(dupes.length === 0, `no number twice — the hung answer, when it lands, is not published again (dupes: ${[...new Set(dupes)].join(',')})`);
  ok(all.every((n, i) => i === 0 || n > all[i - 1]), `numbers are published in spoken order (${all.join(',')})`);
  const c = pipe.coverage();
  ok(c.droppedSec === 0, `nothing dropped (${JSON.stringify(c)})`);
  const last = progress[progress.length - 1];
  ok(!!last && last.backlog_s === 0, `the final report is caught up (${JSON.stringify(last)})`);
}

// ── B: a turn closed behind a hung request is finished by a fresh final, not after the hang ─────
{
  let requests = 0;
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
    const n = ++requests;
    if (n === 1) { await sleep(3000); return decode(pcm); }
    await sleep(20);
    return decode(pcm);
  };
  const confirmed: TranscriptSegment[] = [];
  const sink: TranscriptSink = { segment: (s) => confirmed.push(s), draft: () => {}, finalize: () => {} };
  const pipe = createGmeetPipeline({
    transcribe, sink,
    config: { submitInterval: 0.1, minAudioDuration: 0.6, sttStallMinMs: 300, sttStallFactor: 1 },
    coverageLogIntervalMs: 0,
  });
  // A short turn, closed at once: its FIRST request hangs; the close must not wait 3 s on it.
  let ts = 1_800_000_000_000;
  for (let k = 1; k <= 5; k++) { pipe.feedAudio(0, 'Me', pcmFor(k), ts); ts += 300; }
  await sleep(250);                                         // the draft goes out and hangs
  pipe.feedAudio(0, 'Me', pcmFor(6), ts + 2500);            // gap → the turn closes behind the hung draft
  await sleep(2200);                                        // the 1.5 s draft stalls at 1.5 s; the hang lasts 3 s
  const got = confirmed.flatMap((s) => numsIn(s.text));
  ok([1, 2, 3, 4, 5].every((n) => got.includes(n)), `a turn closed behind a hung request is finished by a fresh final before the hung request answers (${JSON.stringify(got)})`);
  await pipe.dispose();
  const all = confirmed.flatMap((s) => numsIn(s.text));
  ok(all.filter((n) => n <= 5).length === 5, `the hung draft's late answer adds nothing (${JSON.stringify(all)})`);
}

console.log(`\n✅ stt-hol: ${checks} checks passed`);
