/**
 * Regression — a slow STT never costs audio (app.dev meeting 26, 2026-10-11).
 *
 * The remote STT answered a few seconds of audio in 5–30 s. The lane lost most of the meeting:
 *   - a closed turn was removed a fixed 12 s after its close, so a deferred final (the draft in
 *     flight, then the owned window resubmitted) answered into a buffer that no longer existed;
 *   - an input gap while a request was in flight detached the buffered stretch and DROPPED it
 *     ("dropped: a request is in flight"), and with no request in flight its answer landed on the
 *     reset live buffer and was discarded as stale;
 *   - sub-second fragments were sent and came back as "Thank you.", which the filter discarded.
 *
 * Each scenario fails on the code before the fix. The first one drives the real pipeline with an
 * STT stub that answers after 8 s of wall time; the rest are timer-free manager scenarios.
 *
 *   tsx src/slow-stt.test.ts
 */
import { createGmeetPipeline, type TranscriptSegment, type TranscriptSink } from './index.js';
import { SpeakerStreamManager } from './speaker-streams.js';
import { setLogger } from './log.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}
const logs: string[] = [];
setLogger((m) => { logs.push(String(m)); });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const SR = 16000;
const NUM = 4800;                                           // 0.3 s per counted number
const pcmFor = (k: number) => new Float32Array(NUM).fill(k / 1000);
/** Decode the counted numbers in a window (the oracle the count fixture uses). */
const decode = (pcm: Float32Array): TranscriptionResult => {
  const nums: { k: number; start: number; end: number }[] = [];
  let i = 0;
  while (i < pcm.length) {
    const v = Math.round(pcm[i] * 1000);
    if (v === 0) { i++; continue; }
    const s = i;
    while (i < pcm.length && Math.round(pcm[i] * 1000) === v) i++;
    nums.push({ k: v, start: s / SR, end: i / SR });
  }
  return {
    text: nums.map((n) => String(n.k)).join(' '), language: 'en', language_probability: 0.99,
    duration: pcm.length / SR, segments: nums.map((n) => ({ start: n.start, end: n.end, text: String(n.k) })),
  };
};
const numsIn = (t: string) => (t.match(/\d+/g) || []).map(Number);

// ── A: the real pipeline against an STT that answers after 8 s ─────────────────────────────────
// Five turns on one channel, each closed (gap onset) while its draft request is in flight. Every
// counted number must reach the transcript exactly once, and the coverage line must show no loss.
{
  const LATENCY_MS = 8000;
  let requests = 0;
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
    requests++;
    await sleep(LATENCY_MS);
    return decode(pcm);
  };
  const confirmed: TranscriptSegment[] = [];
  const sink: TranscriptSink = { segment: (s) => confirmed.push(s), draft: () => {}, finalize: () => {} };
  const pipe = createGmeetPipeline({
    transcribe, sink,
    config: { submitInterval: 0.25, minAudioDuration: 1 },
    coverageLogIntervalMs: 0,
  });
  const TURNS = 5, PER_TURN = 8;                            // 2.4 s of counting per turn
  let k = 1, ts = 1_800_000_000_000;
  for (let t = 0; t < TURNS; t++) {
    for (let j = 0; j < PER_TURN; j++, k++) { pipe.feedAudio(0, 'Alice', pcmFor(k), ts); ts += 300; }
    await sleep(600);                                       // the turn's draft goes out and stays in flight
    ts += 1500;                                             // > onset gap: the next frame closes this turn
  }
  // Let every deferred final come back through the slow STT (draft 8 s + final 8 s), with margin.
  await sleep(2 * LATENCY_MS + 4000);
  await pipe.dispose();
  const all = confirmed.flatMap((s) => numsIn(s.text));
  const missing: number[] = [];
  for (let i = 1; i < k; i++) if (!all.includes(i)) missing.push(i);
  const dupes = all.filter((n, i) => all.indexOf(n) !== i);
  ok(missing.length === 0, `8 s STT: every counted number reaches the transcript (missing ${missing.length}: ${missing.slice(0, 12).join(',')}; ${requests} requests)`);
  ok(dupes.length === 0, `8 s STT: no number is transcribed twice (dupes: ${[...new Set(dupes)].join(',')})`);
  const c = pipe.coverage();
  ok(c.droppedSec === 0, `coverage shows nothing dropped (${JSON.stringify(c)})`);
  ok(Math.abs(c.capturedSec - (k - 1) * 0.3) < 0.05 && c.transcribedSec >= c.capturedSec - 0.05,
    `coverage: captured ${c.capturedSec}s, transcribed ${c.transcribedSec}s of ${((k - 1) * 0.3).toFixed(1)}s spoken`);
  ok(logs.some((l) => l.startsWith('[COVERAGE] captured=')), 'the per-meeting [COVERAGE] line is written at dispose');
}

// ── B: an input gap while a request is in flight detaches the stretch — it is finished, not dropped ──
{
  const mgr = new SpeakerStreamManager();
  const requests: { key: string; pcm: Float32Array }[] = [];
  const confirmed: string[] = [];
  mgr.onSegmentReady = (key, _n, pcm) => { requests.push({ key, pcm }); };
  mgr.onSegmentConfirmed = (_id, _n, text) => confirmed.push(text);
  const SID = 'ch-0:1';
  mgr.addSpeaker(SID, 'Alice');
  let ts = 1_800_000_000_000, k = 1;
  for (let j = 0; j < 10; j++, k++) { mgr.feedAudio(SID, pcmFor(k), ts); ts += 300; }
  await (mgr as unknown as { trySubmit(id: string): Promise<void> }).trySubmit(SID);   // request 1 in flight
  for (let j = 0; j < 4; j++, k++) { mgr.feedAudio(SID, pcmFor(k), ts); ts += 300; }   // 11..14 arrive meanwhile
  logs.length = 0;
  for (let j = 0; j < 4; j++, k++) { mgr.feedAudio(SID, pcmFor(k), ts + 3000 + j * 300); } // 3 s input gap → new stretch 15..18
  ok(!logs.some((l) => l.includes('dropped: a request is in flight')), 'the input gap no longer drops the in-flight stretch');
  // Request 1 answers (it covers 1..10). The detached stretch now owns 1..14 and finishes as a final.
  mgr.handleTranscriptionResult(requests[0].key, decode(requests[0].pcm).text, 3, decode(requests[0].pcm).segments, 'en');
  ok(requests.length === 2, `the detached stretch is resubmitted whole as its final window (${requests.length} requests)`);
  const fin = requests[1];
  mgr.handleTranscriptionResult(fin.key, decode(fin.pcm).text, undefined, decode(fin.pcm).segments, 'en');
  await mgr.flushSpeaker(SID, true);                         // the live stretch (15..18) closes
  const live = requests[2];
  ok(!!live && live.key !== fin.key, 'the live stretch is requested under its own route (its answer cannot land on the old one)');
  mgr.handleTranscriptionResult(live.key, decode(live.pcm).text, undefined, decode(live.pcm).segments, 'en');
  const all = confirmed.flatMap(numsIn);
  ok([...Array(18).keys()].every((i) => all.includes(i + 1)) && all.length === 18,
    `1..18 all reach the transcript once across the gap (${JSON.stringify(all)})`);
  ok(mgr.coverage().droppedSec === 0, `nothing counted as dropped (${JSON.stringify(mgr.coverage())})`);
  mgr.removeAll();
}

// ── C: an input gap with NO request in flight — the detached answer reaches the detached stretch ──
{
  const mgr = new SpeakerStreamManager();
  const requests: { key: string; pcm: Float32Array }[] = [];
  const confirmed: string[] = [];
  mgr.onSegmentReady = (key, _n, pcm) => { requests.push({ key, pcm }); };
  mgr.onSegmentConfirmed = (_id, _n, text) => confirmed.push(text);
  const SID = 'ch-0:2';
  mgr.addSpeaker(SID, 'Bob');
  let ts = 1_800_000_000_000;
  for (let k = 1; k <= 5; k++) { mgr.feedAudio(SID, pcmFor(k), ts); ts += 300; }
  mgr.feedAudio(SID, pcmFor(6), ts + 3000);                  // gap → stretch 1..5 detached + submitted
  ok(requests.length === 1, 'the detached stretch is submitted at once');
  await sleep(0);
  mgr.handleTranscriptionResult(requests[0].key, decode(requests[0].pcm).text, undefined, decode(requests[0].pcm).segments, 'en');
  ok(confirmed.length === 1 && confirmed[0] === '1 2 3 4 5', `its answer is published, not discarded as stale (${JSON.stringify(confirmed)})`);
  mgr.removeAll();
}

// ── D: the held-audio cap — past it, new audio is dropped LOUDLY with the seconds lost ───────────
{
  const mgr = new SpeakerStreamManager({ maxHeldAudioSec: 2 });
  mgr.onSegmentReady = () => {};
  const SID = 'ch-0:3';
  mgr.addSpeaker(SID, 'Carol');
  let ts = 1_800_000_000_000;
  for (let k = 1; k <= 7; k++) { mgr.feedAudio(SID, pcmFor(k), ts); ts += 300; }       // 2.1 s… over the cap only once in flight
  await (mgr as unknown as { trySubmit(id: string): Promise<void> }).trySubmit(SID);   // in flight, never answers
  logs.length = 0;
  for (let k = 8; k <= 12; k++) { mgr.feedAudio(SID, pcmFor(k), ts); ts += 300; }      // 1.5 s more
  const c = mgr.coverage();
  ok(Math.abs(c.droppedSec - 1.5) < 0.05, `the 1.5 s over the cap are counted as dropped (${JSON.stringify(c)})`);
  ok(logs.some((l) => l.includes('[AUDIO-DROPPED]') && l.includes('behind a request that has not answered')),
    `the loss is logged loudly with its seconds (${logs.filter((l) => l.includes('DROPPED')).join(' | ')})`);
  mgr.removeAll();
}

// ── E: sub-second final windows are not sent; a 1 s+ window is ─────────────────────────────────
{
  const mgr = new SpeakerStreamManager();
  let sent = 0;
  mgr.onSegmentReady = () => { sent++; };
  mgr.addSpeaker('a', 'Speaker');
  mgr.feedAudio('a', new Float32Array(Math.round(0.5 * SR)).fill(0.05), 1_800_000_000_000);
  await mgr.flushSpeaker('a', true);
  ok(sent === 0 && mgr.coverage().shortSec === 0.5, `a 0.5 s fragment is not submitted and is counted as short (${JSON.stringify(mgr.coverage())})`);
  mgr.addSpeaker('b', 'Speaker');
  mgr.feedAudio('b', new Float32Array(Math.round(1.2 * SR)).fill(0.05), 1_800_000_000_000);
  await mgr.flushSpeaker('b', true);
  ok(sent === 1, 'a 1.2 s final window is submitted');
  mgr.removeAll();
}

// ── F: a closed turn is settled only once its deferred final has answered ──────────────────────
{
  const mgr = new SpeakerStreamManager();
  const requests: string[] = [];
  mgr.onSegmentReady = (key) => { requests.push(key); };
  mgr.addSpeaker('t', 'Dan');
  let ts = 1_800_000_000_000;
  for (let k = 1; k <= 8; k++) { mgr.feedAudio('t', pcmFor(k), ts); ts += 300; }
  await (mgr as unknown as { trySubmit(id: string): Promise<void> }).trySubmit('t');
  await mgr.flushSpeaker('t', true);
  ok(!mgr.isSettled('t'), 'a turn closed while its draft is in flight is not settled');
  mgr.handleTranscriptionResult('t', '1 2 3 4 5 6 7 8', 2.4);
  ok(!mgr.isSettled('t') && requests.length === 2, 'after the draft answers, its final is in flight — still not settled');
  mgr.handleTranscriptionResult('t', '1 2 3 4 5 6 7 8', 2.4);
  ok(mgr.isSettled('t'), 'once the final answers the turn is settled and may be freed');
  mgr.removeAll();
}

console.log(`\n✅ slow-stt: ${checks} checks passed`);
