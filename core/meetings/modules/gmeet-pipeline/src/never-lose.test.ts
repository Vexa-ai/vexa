/**
 * Regression — captured audio is never lost to a slow or failing STT (app.dev voice note 76,
 * 2026-10-13: captured=722.9s, transcribed=146.4s; every window that failed four attempts was
 * DROPPED, and the one request that went out after a long hang carried 160 s of audio).
 *
 *   A  a window sent to STT never exceeds the per-request cap, however far behind the lane is;
 *   B  windows that fail during an STT outage are retried with backoff and land once it recovers;
 *   C  on dispose, the lane retries every unfinished window before it reports the note complete;
 *   D  past the final-pass deadline, what could not be transcribed is named by time range, loudly.
 *
 *   tsx src/never-lose.test.ts
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
setLogger((m) => { logs.push(String(m)); if (process.env.DBG) console.log('LOG', m); });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const SR = 16000;
const NUM = 4800;                                           // 0.3 s per counted number
// Amplitudes offset by 20: clearly speech against the lane's noise floor (speech gate).
const pcmFor = (k: number) => new Float32Array(NUM).fill((k + 20) / 1000);
const decode = (pcm: Float32Array): TranscriptionResult => {
  const nums: { k: number; start: number; end: number }[] = [];
  let i = 0;
  while (i < pcm.length) {
    const v = Math.round(pcm[i] * 1000);
    if (v === 0) { i++; continue; }
    const s = i;
    while (i < pcm.length && Math.round(pcm[i] * 1000) === v) i++;
    nums.push({ k: v - 20, start: s / SR, end: i / SR });
  }
  const t = (k: number) => `alpha${k} bravo${k * 7} charlie${k * 13}`;
  return {
    text: nums.map((n) => t(n.k)).join(' '), language: 'en', language_probability: 0.99,
    duration: pcm.length / SR, segments: nums.map((n) => ({ start: n.start, end: n.end, text: t(n.k) })),
  };
};
const numsIn = (t: string) => (t.match(/alpha(\d+)/g) || []).map((w) => Number(w.slice(5)));
const fault = () => Object.assign(new Error('stt unavailable (HTTP 503)'), { source: 'stt', kind: 'unavailable', status: 503, retryable: true });

function verdict(label: string, confirmed: TranscriptSegment[], total: number) {
  const all = confirmed.flatMap((s) => numsIn(s.text));
  const missing: number[] = [];
  for (let i = 1; i <= total; i++) if (!all.includes(i)) missing.push(i);
  const dupes = all.filter((n, i) => all.indexOf(n) !== i);
  ok(missing.length === 0, `${label}: every counted number reaches the transcript (missing: ${missing.join(',')})`);
  ok(dupes.length === 0, `${label}: no number twice (dupes: ${[...new Set(dupes)].join(',')})`);
  ok(all.every((n, i) => i === 0 || n > all[i - 1]), `${label}: published in spoken order (${all.join(',')})`);
}
const sinkInto = (arr: TranscriptSegment[]): TranscriptSink => ({ segment: (s) => arr.push(s), draft: () => {}, finalize: () => {} });

// ── A: far behind, the windows sent stay within the cap ───────────────────────────────────────────
{
  let requests = 0;
  const sent: number[] = [];
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
    const n = ++requests;
    sent.push(pcm.length / SR);
    await sleep(n === 1 ? 6000 : 25);
    return decode(pcm);
  };
  const confirmed: TranscriptSegment[] = [];
  const pipe = createGmeetPipeline({
    transcribe, sink: sinkInto(confirmed), coverageLogIntervalMs: 0,
    config: { submitInterval: 0.1, minAudioDuration: 0.6, maxBufferDuration: 3, sttStallMinMs: 5000, sttStallFactor: 1 },
  });
  let ts = 1_800_000_000_000;
  for (let k = 1; k <= 40; k++) { pipe.feedAudio(0, 'Me', pcmFor(k), ts); ts += 300; await sleep(100); }
  await sleep(1500);
  const longest = Math.max(...sent);
  ok(longest <= 3.01, `no request carries more than the 3 s cap (longest ${longest.toFixed(2)} s over ${sent.length} requests)`);
  ok(confirmed.flatMap((s) => numsIn(s.text)).length >= 30, `the backlog drains in capped windows while the first request hangs (${confirmed.flatMap((s) => numsIn(s.text)).length}/40)`);
  await pipe.dispose();
  verdict('A', confirmed, 40);
  ok(pipe.coverage().droppedSec === 0, `A: nothing dropped (${JSON.stringify(pipe.coverage())})`);
}

// ── B: an STT outage — failed windows are retried and land once it recovers ───────────────────────
{
  const outageUntil = Date.now() + 2500;
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
    await sleep(20);
    if (Date.now() < outageUntil) throw fault();
    return decode(pcm);
  };
  const confirmed: TranscriptSegment[] = [];
  const pipe = createGmeetPipeline({
    transcribe, sink: sinkInto(confirmed), coverageLogIntervalMs: 0, onError: () => {},
    config: { submitInterval: 0.1, minAudioDuration: 0.6, maxBufferDuration: 3, finalRetryBaseMs: 200, finalRetryMaxMs: 400 },
  });
  // Short turns separated by gaps: each closes (a final) during the outage.
  let ts = 1_800_000_000_000, k = 1;
  for (let turn = 0; turn < 5; turn++) {
    for (let j = 0; j < 5; j++, k++) { pipe.feedAudio(0, 'Me', pcmFor(k), ts); ts += 300; await sleep(60); }
    ts += 2000;
  }
  await sleep(4000);                                        // outage over; retries land
  verdict('B (before dispose)', confirmed, k - 1);
  await pipe.dispose();
  ok(pipe.coverage().droppedSec === 0, `B: nothing dropped across the outage (${JSON.stringify(pipe.coverage())})`);
}

// ── C: STT down until Stop — the final pass at dispose transcribes every window ───────────────────
{
  let disposing = false;
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
    await sleep(15);
    if (!disposing) throw fault();
    return decode(pcm);
  };
  const confirmed: TranscriptSegment[] = [];
  const pipe = createGmeetPipeline({
    transcribe, sink: sinkInto(confirmed), coverageLogIntervalMs: 0, onError: () => {},
    config: { submitInterval: 0.1, minAudioDuration: 0.6, maxBufferDuration: 3, finalRetryBaseMs: 60_000, finalRetryMaxMs: 60_000 },
  });
  let ts = 1_800_000_000_000;
  for (let k = 1; k <= 30; k++) { pipe.feedAudio(0, 'Me', pcmFor(k), ts); ts += 300; await sleep(30); }
  await sleep(500);
  ok(confirmed.length === 0, 'nothing transcribed while STT is down');
  disposing = true;
  await pipe.dispose();
  verdict('C', confirmed, 30);
  ok(pipe.coverage().droppedSec === 0, `C: the final pass leaves nothing dropped (${JSON.stringify(pipe.coverage())})`);
}

// ── D: STT never recovers — past the deadline the untranscribed ranges are named, not hidden ──────
{
  const transcribe = async (): Promise<TranscriptionResult> => { await sleep(10); throw fault(); };
  const confirmed: TranscriptSegment[] = [];
  const pipe = createGmeetPipeline({
    transcribe, sink: sinkInto(confirmed), coverageLogIntervalMs: 0, onError: () => {}, finalPassMs: 600,
    config: { submitInterval: 0.1, minAudioDuration: 0.6, maxBufferDuration: 3, finalRetryBaseMs: 100, finalRetryMaxMs: 100 },
  });
  let ts = 1_800_000_000_000;
  for (let k = 1; k <= 10; k++) { pipe.feedAudio(0, 'Me', pcmFor(k), ts); ts += 300; }
  logs.length = 0;
  const t0 = Date.now();
  await pipe.dispose();
  ok(Date.now() - t0 < 3000, `dispose gives up at its final-pass deadline (${Date.now() - t0} ms)`);
  const lines = logs.filter((l) => l.includes('[AUDIO-UNTRANSCRIBED]'));
  ok(lines.length > 0 && lines.every((l) => /\d{4}-\d\d-\d\dT[\d:.]+Z–\d{4}-\d\d-\d\dT[\d:.]+Z/.test(l)),
    `each untranscribed range is logged with its capture times (${lines.join(' | ')})`);
  ok(Math.abs(pipe.coverage().droppedSec - 3) < 0.05, `the loss is counted, not hidden (${JSON.stringify(pipe.coverage())})`);
}

// ── E: a continuous mic (silence frames between phrases) with pipelined drafts + pause finals ─────
// Pipelined answers move the window start, which turns the other requests in flight stale. A pause
// final deferred behind only-stale requests used to wait forever (published after a 120 s hold).
{
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => { await sleep(1200); return decode(pcm); };
  const confirmed: TranscriptSegment[] = [];
  const at: number[] = [];
  const pipe = createGmeetPipeline({
    transcribe, sink: { segment: (s) => { confirmed.push(s); at.push(Date.now()); }, draft: () => {}, finalize: () => {} },
    coverageLogIntervalMs: 0,
    config: { submitInterval: 0.2, minAudioDuration: 0.3, confirmThreshold: 1, pipelineDrafts: true, pauseFinalMs: 300,
      idleTimeoutSec: 1, maxBufferDuration: 4 },
  });
  let ts = 1_800_000_000_000, k = 1;
  const silence = new Float32Array(NUM);
  const spokenAt = new Map<number, number>();
  for (let phrase = 0; phrase < 5; phrase++) {
    for (let j = 0; j < 5; j++, k++) { pipe.feedAudio(0, 'Me', pcmFor(k), ts); spokenAt.set(k, Date.now()); ts += 300; await sleep(60); }
    for (let j = 0; j < 3; j++) { pipe.feedAudio(0, 'Me', silence, ts); ts += 300; await sleep(60); }
  }
  await sleep(4000);
  const seen = confirmed.flatMap((s) => numsIn(s.text));
  ok(seen.length >= 24, `pipelined drafts on a continuous mic publish within ~4 s of the last phrase (${seen.length}/25)`);
  ok(!logs.some((l) => l.includes('[TRANSCRIPT-GAP]')), 'no stretch is held back waiting for an answer that will never be used');
  await pipe.dispose();
  verdict('E', confirmed, 25);
}

console.log(`\n✅ never-lose: ${checks} checks passed`);
