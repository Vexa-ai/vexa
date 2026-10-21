/**
 * Regression — the 30 s hard cap finalizes a Zoom window from its own request and keeps the rest.
 *
 * When a turn's unconfirmed audio passes `maxBufferDuration` with nothing confirmed, the lane used to
 * force-flush: emit the last transcript (which ends at the previous request, or does not exist) and
 * reset the buffer. Every sample fed since that request was dropped, and the cut fell wherever the
 * cap happened to land — usually mid-word. Long Zoom turns reach the cap often.
 *
 * With `callbackStampedFrames`, the cap submits the window up to the quietest frame of its last 4 s
 * as a final request. Its answer becomes the confirmed segment for that window (ending at the cut's
 * capture stamp), and the audio after the cut stays buffered for the next window. Feeders without
 * the flag keep the force-flush.
 *
 *   tsx src/hard-cap-cut.test.ts
 */
import { SpeakerStreamManager } from './speaker-streams.js';
import { setLogger } from './log.js';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}
setLogger(() => {});

const SR = 16000, FRAME = 4096, FRAME_MS = (FRAME / SR) * 1000;
const frame = (amp: number): Float32Array => { const f = new Float32Array(FRAME); for (let i = 0; i < FRAME; i++) f[i] = amp * Math.sin(i / 7); return f; };
const T0 = 1_800_000_000_000;
const N = 118, QUIET = 112;                                   // 30.2 s of audio; a pause at frame 112 (28.7 s)

function harness(config?: { callbackStampedFrames?: boolean }, spacingMs = 2 * FRAME_MS) {
  const mgr = new SpeakerStreamManager(config);
  const SID = 'ch-4:1';
  const requests: number[] = [];
  const confirmed: { text: string; start: number; end: number }[] = [];
  mgr.onSegmentReady = (_id, _n, audio) => { requests.push(audio.length / FRAME); };
  mgr.onSegmentConfirmed = (_id, _n, text, start, end) => confirmed.push({ text, start, end });
  mgr.addSpeaker(SID, 'Speaker');
  let k = 0;
  const feed = (n: number) => { for (let i = 0; i < n; i++, k++) mgr.feedAudio(SID, frame(k === QUIET ? 0.003 : 0.1), T0 + k * spacingMs); };
  const trySubmit = () => (mgr as unknown as { trySubmit(id: string): Promise<void> }).trySubmit(SID);
  return { mgr, SID, requests, confirmed, feed, trySubmit };
}

// ── A: the cap finalizes the window at its quietest frame and keeps the audio after it ─────────
{
  const h = harness({ callbackStampedFrames: true });
  h.feed(N);                                                  // a starved turn: 30.2 s of audio over 60 s
  await h.trySubmit();                                        // over the cap, nothing confirmed
  ok(h.requests.length === 1 && h.requests[0] === QUIET,
    `the cap sends the window up to its quietest frame as one request (${JSON.stringify(h.requests)} frames)`);
  h.mgr.handleTranscriptionResult(h.SID, 'the capped window', 28.7, [{ text: 'the capped window', start: 0, end: 28.7 }], 'en');
  const c = h.confirmed[0];
  ok(h.confirmed.length === 1 && c.text === 'the capped window', 'the answer is published as the confirmed segment for that window');
  ok(Math.abs(c.end - (T0 + QUIET * 2 * FRAME_MS)) < 1, 'it ends at the capture stamp of the cut');
  ok(Math.abs(h.mgr.getBufferStartMs(h.SID) - (T0 + QUIET * 2 * FRAME_MS)) < 1, 'the next window starts at the cut');
  h.feed(4);                                                  // the turn continues
  await h.trySubmit();
  ok(h.requests.length === 2 && h.requests[1] === N - QUIET + 4,
    `the audio after the cut reaches the next request with the new frames (${h.requests[1]} frames)`);
  h.mgr.removeAll();
}

// ── B: feeders that do not opt in keep the force-flush (contiguous frames, as Google Meet feeds) ──
{
  const h = harness(undefined, FRAME_MS);
  h.feed(N);
  await h.trySubmit();
  ok(h.requests.length === 0 && h.confirmed.length === 0,
    'without the flag the cap force-flushes as before (no request, nothing to emit)');
  h.mgr.removeAll();
}

console.log(`\n✅ hard-cap-cut: ${checks} checks passed`);
