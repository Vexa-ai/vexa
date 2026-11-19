/**
 * An overloaded STT is not answered with a resend storm, and no audio is lost while the bot backs
 * off (incident 2026-10-14).
 *
 * The shared STT gateway answered a few 429s and slow reads. The client retried each request on its
 * own schedule and the lane's STT-STALLED logic sent windows again beside the slow ones: the bot's
 * rate went from 37 to 120 requests a minute, the gateway's slots filled, it refused 98 requests in
 * two minutes and a live meeting lost 11 windows.
 *
 * A local HTTP server stands in for the STT (WAV upload; each number is a constant-amplitude run it
 * decodes back). It is overloaded for a stretch, then answers slowly. Delays are scaled down.
 *
 *   tsx src/stt-overload.test.ts      (needs @vexa/transcribe-whisper built: its dist)
 */
import { createServer, type ServerResponse } from 'node:http';
import { createGmeetPipeline, SpeakerStreamManager, type TranscriptSegment, type TranscriptSink } from './index.js';
import { setLogger } from './log.js';
import { TranscriptionClient, SttGate, setLogger as setClientLogger, type TranscriptionResult } from '@vexa/transcribe-whisper';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const guard = setTimeout(() => { console.error('❌ stt-overload: timed out after 90 s'); process.exit(1); }, 90_000);
guard.unref();
const SR = 16000;
const NUM = 4800;                                           // 0.3 s per counted number
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
  const say = (k: number) => `alpha${k} bravo${k * 7} charlie${k * 13}`;
  return {
    text: nums.map((n) => say(n.k)).join(' '), language: 'en', language_probability: 0.99,
    duration: pcm.length / SR, segments: nums.map((n) => ({ start: n.start, end: n.end, text: say(n.k) })),
  };
};
const numsIn = (t: string) => (t.match(/alpha(\d+)/g) || []).map((w) => Number(w.slice(5)));

/** The WAV inside a multipart upload → float PCM. */
function wavFromMultipart(body: Buffer): Float32Array {
  const at = body.indexOf('RIFF');
  const size = body.readUInt32LE(at + 40);
  const out = new Float32Array(size / 2);
  for (let i = 0; i < out.length; i++) out[i] = body.readInt16LE(at + 44 + i * 2) / 0x7fff;
  return out;
}

// ── A: three people talk while the STT is overloaded, then slow ──────────────────────────────────
{
  const logs: { line: string; backingOff: boolean }[] = [];
  const gate = new SttGate({ maxConcurrent: 4, breakerThreshold: 3, backoffBaseMs: 200, backoffMaxMs: 500 });
  const record = (m: unknown) => { logs.push({ line: String(m), backingOff: gate.backingOff() }); if (process.env.DBG) console.log('LOG', m); };
  setLogger(record);
  setClientLogger(record);

  let overloaded = false;
  let active = 0, maxActive = 0;
  const sentAt: number[] = [];
  const server = createServer((req, res: ServerResponse) => {
    const parts: Buffer[] = [];
    req.on('data', (c: Buffer) => parts.push(c));
    req.on('end', () => {
      sentAt.push(Date.now());
      active++; maxActive = Math.max(maxActive, active);
      res.on('close', () => { active--; });
      if (overloaded) { res.writeHead(429); res.end('busy'); return; }
      const result = decode(wavFromMultipart(Buffer.concat(parts)));
      setTimeout(() => {                                    // slow reads: past the stall threshold
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify(result));
      }, 500);
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const client = new TranscriptionClient({ serviceUrl: url, audioFormat: 'wav', maxRetries: 3, retryDelayMs: 50, gate });

  const confirmed: TranscriptSegment[] = [];
  const sink: TranscriptSink = { segment: (s) => confirmed.push(s), draft: () => {}, finalize: () => {} };
  const pipe = createGmeetPipeline({
    transcribe: (pcm, prompt) => client.transcribe(pcm, undefined, prompt),
    sttPressure: client.pressure(),
    sink,
    config: { submitInterval: 0.1, minAudioDuration: 0.6, sttStallMinMs: 300, sttStallFactor: 0.2, maxBufferDuration: 4 },
    coverageLogIntervalMs: 0,
    onsetGapMs: 5000,
  });

  // Three channels, one person each, 30 numbers (9 s of speech) apiece, fed over ~4.5 s of wall
  // clock. Numbers are distinct per channel (channel c says c*100 + i).
  const TOTAL = 30;
  let ts = 1_800_000_000_000;
  const t0 = Date.now();
  for (let i = 1; i <= TOTAL; i++) {
    if (i === 6) overloaded = true;                         // the gateway saturates…
    if (i === 20) overloaded = false;                       // …and recovers, answering slowly
    for (let c = 0; c < 3; c++) pipe.feedAudio(c, `P${c}`, pcmFor(c * 100 + i), ts);
    ts += 300;
    await sleep(150);
  }
  await pipe.dispose();
  server.close();
  const wallMin = (Date.now() - t0) / 60_000;

  const stalledWhileHeld = logs.filter((l) => l.line.includes('[STT-STALLED]') && l.backingOff);
  ok(stalledWhileHeld.length === 0, `no window is sent again while the client is backing off (${stalledWhileHeld.length} resends during back-off)`);
  ok(maxActive <= 4, `requests in flight never exceed the gate's bound (${maxActive})`);
  const perMin = sentAt.length / wallMin;
  ok(sentAt.length <= 90, `the request count stays bounded: ${sentAt.length} requests (${perMin.toFixed(0)}/min scaled) for 3 × ${TOTAL} numbers`);
  const all = confirmed.flatMap((s) => numsIn(s.text));
  const missing: number[] = [];
  for (let c = 0; c < 3; c++) for (let i = 1; i <= TOTAL; i++) if (!all.includes(c * 100 + i)) missing.push(c * 100 + i);
  ok(missing.length === 0, `every number spoken during the overload reaches the transcript (missing: ${missing.join(',')})`);
  const cov = pipe.coverage();
  ok(cov.droppedSec === 0, `no audio dropped (${JSON.stringify(cov)})`);
  const resolved = cov.transcribedSec + cov.noSpeechSec + cov.shortSec;
  ok(Math.abs(resolved - cov.capturedSec) < 0.2, `coverage = 1 after recovery (resolved ${resolved}s of ${cov.capturedSec}s captured)`);
}

// ── B: at most ONE duplicate per stream, however long the request hangs ──────────────────────────
{
  setLogger(() => {});
  let calls = 0;
  const mgr = new SpeakerStreamManager({ submitInterval: 0.05, minAudioDuration: 0.6, sttStallMinMs: 100, sttStallFactor: 0.1 });
  mgr.onSegmentReady = () => { calls++; };                  // never answers
  mgr.addSpeaker('s', 'S');
  let ts = 1_800_000_000_000;
  for (let i = 1; i <= 6; i++) { mgr.feedAudio('s', pcmFor(i), ts); ts += 300; }
  await sleep(1200);                                        // ~10 stall thresholds
  mgr.removeAll();
  ok(calls === 2, `a hung request gets one duplicate beside it, not a chain of them (${calls} requests)`);
}

// ── C: past the held-audio bound, the lost stretch is marked as a missing range, never silent ───
{
  const lines: string[] = [];
  setLogger((m) => lines.push(String(m)));
  const mgr = new SpeakerStreamManager({ submitInterval: 0.05, minAudioDuration: 0.6, maxHeldAudioSec: 1.5, sttStallMinMs: 60_000 });
  mgr.onSegmentReady = () => {};                            // never answers
  mgr.addSpeaker('s', 'S');
  let ts = 1_800_000_000_000;
  for (let i = 1; i <= 4; i++) { mgr.feedAudio('s', pcmFor(i), ts); ts += 300; }
  await sleep(120);                                         // the window goes out and hangs
  for (let i = 5; i <= 12; i++) { mgr.feedAudio('s', pcmFor(i), ts); ts += 300; }
  const ranges = mgr.missingRanges();
  const coveredSec = ranges.reduce((a, r) => a + (r.endMs - r.startMs) / 1000, 0);
  ok(ranges.length >= 1 && Math.abs(coveredSec - mgr.coverage().droppedSec) < 0.05,
    `audio past the bound is recorded as a missing range (${JSON.stringify(ranges)}, dropped ${mgr.coverage().droppedSec}s)`);
  ok(lines.some((l) => l.includes('[AUDIO-DROPPED]') && /\d{4}-\d\d-\d\dT/.test(l)), 'the loss line names the missing time range');
  mgr.removeAll();
}

console.log(`\n✅ stt-overload: ${checks} checks passed`);
