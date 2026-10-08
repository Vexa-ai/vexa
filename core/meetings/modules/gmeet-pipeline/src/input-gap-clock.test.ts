/**
 * Regression — a callback-stamped feeder's input gap is measured on ONE clock.
 *
 * Zoom per-track capture stamps every frame at capture-callback time (#1774). On a starved page,
 * callbacks arrive late or not at all, so consecutive frames of one continuous turn are spaced by
 * more than the 256 ms of audio each carries, while every gap stays under the lane's 1 s turn-onset
 * gap. Those stamps run ahead of `windowStartMs + buffered samples` by construction.
 *
 * `feedAudio` compared each new stamp with `windowStartMs + buffered samples`. On a starved page
 * that difference grows with every frame, crosses 2 s within a few seconds of speech, and the guard
 * detaches the live buffer mid-turn, over and over. The detached audio is submitted under the
 * speaker id, its response lands on the reset live buffer and is discarded as stale, so that speech
 * never reaches the transcript.
 *
 * With `callbackStampedFrames` the guard compares the stamp with the end of the previous frame.
 * Feeders that do not set it keep the sample-count comparison unchanged.
 *
 * Model-free and timer-free: frames are fed with explicit stamps, nothing waits for the submit
 * interval, and the turn is closed with an explicit flush. We count the audio the manager hands to
 * transcription.
 *
 *   tsx src/input-gap-clock.test.ts
 */
import { SpeakerStreamManager } from './speaker-streams.js';
import { setLogger } from './log.js';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}

const logs: string[] = [];
setLogger((m) => { logs.push(String(m)); });

const SR = 16000, FRAME = 4096, FRAME_MS = (FRAME / SR) * 1000;   // the per-track ScriptProcessor frame
const speech = (): Float32Array => { const f = new Float32Array(FRAME); for (let i = 0; i < FRAME; i++) f[i] = 0.1 * Math.sin(i / 7); return f; };
const ZOOM = { callbackStampedFrames: true } as const;

function harness(config?: { callbackStampedFrames?: boolean }) {
  const mgr = new SpeakerStreamManager(config);
  const submitted: number[] = [];                       // samples per transcription request
  mgr.onSegmentReady = (_id, _name, audio) => { submitted.push(audio.length); };
  return { mgr, submitted };
}

// ── Scenario 1: a starved page — every other capture callback is lost ─────────────────────────
// 40 frames of one continuous turn. Each frame is stamped at its callback's wall time; callbacks
// arrive every 512 ms, so consecutive frames are 512 ms apart (well under the 1 s onset gap) but
// carry 256 ms of audio each. All 40 frames must reach transcription when the turn closes.
{
  const { mgr, submitted } = harness(ZOOM);
  const SID = 'ch-4:1';
  mgr.addSpeaker(SID, 'Speaker');
  const t0 = 1_800_000_000_000;
  const N = 40;
  for (let k = 0; k < N; k++) mgr.feedAudio(SID, speech(), t0 + k * 2 * FRAME_MS);
  const beforeFlush = submitted.length;
  await mgr.flushSpeaker(SID, true);
  mgr.removeAll();

  ok(beforeFlush === 0, `starved page: no input-gap detach inside one continuous turn (got ${beforeFlush} mid-turn submissions)`);
  const total = submitted.reduce((a, b) => a + b, 0);
  ok(submitted.length === 1 && total === N * FRAME,
    `starved page: the turn close hands ALL ${N} frames to transcription in one window (got ${submitted.length} request(s), ${total / FRAME} frames)`);
}

// ── Scenario 2: randomly lost callbacks (15%) plus sub-second jitter ──────────────────────────
// Same property under a noisier starved cadence: gaps of 256–768 ms, never a 1 s onset gap.
{
  const { mgr, submitted } = harness(ZOOM);
  const SID = 'ch-5:1';
  mgr.addSpeaker(SID, 'Speaker');
  let seed = 7; const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  let t = 1_800_000_000_000; const N = 60;
  for (let k = 0; k < N; k++) {
    mgr.feedAudio(SID, speech(), t);
    t += FRAME_MS * (rnd() < 0.15 ? 2 : 1) + Math.floor(rnd() * 250);
  }
  const beforeFlush = submitted.length;
  await mgr.flushSpeaker(SID, true);
  mgr.removeAll();
  ok(beforeFlush === 0, `lost callbacks + jitter: no mid-turn detach (got ${beforeFlush})`);
  ok(submitted.reduce((a, b) => a + b, 0) === N * FRAME, 'lost callbacks + jitter: every fed frame reaches transcription');
}

// ── Scenario 3: the guard still does its job — a real > 2 s discontinuity closes the stretch ──
// The next frame is stamped 3 s after the previous frame ended. The buffered stretch must be closed
// (submitted on its own) before the new audio is appended, and the detach must be logged.
{
  const { mgr, submitted } = harness(ZOOM);
  const SID = 'ch-6:1';
  mgr.addSpeaker(SID, 'Speaker');
  const t0 = 1_800_000_000_000;
  for (let k = 0; k < 10; k++) mgr.feedAudio(SID, speech(), t0 + k * FRAME_MS);
  logs.length = 0;
  mgr.feedAudio(SID, speech(), t0 + 10 * FRAME_MS + 3000);
  ok(submitted.length === 1 && submitted[0] === 10 * FRAME,
    `a 3 s input gap closes the previous stretch on its own (got ${submitted.length} request(s), first ${(submitted[0] ?? 0) / FRAME} frames)`);
  ok(logs.some((l) => l.includes('Input gap') && l.includes('3.0s')), 'the detach is logged with its gap');
  mgr.removeAll();
}

// ── Scenario 4: feeders that do not opt in keep today's comparison ────────────────────────────
// Google Meet and every other per-channel feeder construct the manager without the flag; the guard
// still compares against `windowStartMs + buffered samples` for them, exactly as before.
{
  const { mgr, submitted } = harness();
  const SID = 'ch-7:1';
  mgr.addSpeaker(SID, 'Speaker');
  const t0 = 1_800_000_000_000;
  for (let k = 0; k < 40; k++) mgr.feedAudio(SID, speech(), t0 + k * 2 * FRAME_MS);
  ok(submitted.length > 0, `without the flag the sample-count comparison is unchanged (${submitted.length} detach(es) on the same stamps)`);
  mgr.removeAll();
}

console.log(`\n✅ input-gap-clock: ${checks} checks passed`);
