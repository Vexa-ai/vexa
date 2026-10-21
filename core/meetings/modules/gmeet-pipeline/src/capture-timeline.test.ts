/**
 * Regression — on a starved Zoom page, segment times follow each frame's capture stamp (#1774).
 *
 * Per-track frames are stamped at capture-callback time. On a starved page half the callbacks are
 * lost, so a turn's frames cover only part of its wall time: here every 256 ms frame is 512 ms after
 * the previous one. The buffer used to place sample `n` at `windowStartMs + n / rate`, a gapless
 * timeline, so a segment 20 s into the buffered audio was stamped 20 s after the turn started
 * although it was captured 40 s after it. Inside a long turn the error grows without bound.
 *
 * With `callbackStampedFrames`, every Whisper offset is mapped through the stamp of the frame that
 * holds it: confirmed segments, the retained window after a confirmation, and the turn's end.
 * Contiguous frames (no lost callbacks) map exactly as before.
 *
 *   tsx src/capture-timeline.test.ts
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
const speech = (): Float32Array => { const f = new Float32Array(FRAME); for (let i = 0; i < FRAME; i++) f[i] = 0.1 * Math.sin(i / 7); return f; };
const T0 = 1_800_000_000_000;
type Seg = { text: string; start: number; end: number };

/** Capture time of buffered sample `n` when frame k is stamped at T0 + k * spacingMs. */
const stampOf = (n: number, spacingMs: number) => T0 + Math.floor(n / FRAME) * spacingMs + ((n % FRAME) / SR) * 1000;

function run(spacingMs: number) {
  const mgr = new SpeakerStreamManager({ callbackStampedFrames: true });
  const SID = 'ch-4:1';
  const confirmed: Seg[] = [];
  mgr.onSegmentConfirmed = (_id, _n, text, start, end) => confirmed.push({ text, start, end });
  mgr.onSegmentReady = () => {};
  mgr.addSpeaker(SID, 'Speaker');
  const N = 100;                                                      // 25.6 s of audio
  for (let k = 0; k < N; k++) mgr.feedAudio(SID, speech(), T0 + k * spacingMs);
  return { mgr, SID, confirmed, N };
}

// ── A: a starved turn (every other callback lost) — segments land at their capture time ────────
{
  const { mgr, SID, confirmed, N } = run(2 * FRAME_MS);
  // Two consecutive answers that agree on their first two segments confirm them (LocalAgreement).
  const a = [{ text: 'alpha beta', start: 0, end: 6 }, { text: 'gamma delta', start: 12, end: 20 }, { text: 'eps', start: 20, end: 25.6 }];
  const b = [a[0], a[1], { text: 'eps zeta', start: 20, end: 25.6 }];
  mgr.handleTranscriptionResult(SID, 'alpha beta gamma delta eps', 25.6, a, 'en');
  mgr.handleTranscriptionResult(SID, 'alpha beta gamma delta eps zeta', 25.6, b, 'en');
  const g = confirmed.find((s) => s.text === 'gamma delta');
  const want = stampOf(12 * SR, 2 * FRAME_MS);
  ok(!!g && Math.abs(g.start - want) < 1,
    `a segment 12 s into the audio starts at its frame's capture stamp (+${((want - T0) / 1000).toFixed(1)} s), not +12.0 s (got +${g ? ((g.start - T0) / 1000).toFixed(1) : '—'} s)`);
  ok(!!g && Math.abs(g.end - stampOf(20 * SR, 2 * FRAME_MS)) < 1, 'its end is the capture stamp of its last sample');
  const retained = mgr.getBufferStartMs(SID);
  ok(Math.abs(retained - stampOf(20 * SR, 2 * FRAME_MS)) < 1,
    `the retained window after the confirmation starts at its first frame's stamp (+${((retained - T0) / 1000).toFixed(1)} s)`);
  await mgr.flushSpeaker(SID, true);
  mgr.handleTranscriptionResult(SID, 'eps zeta', 5.6, undefined, 'en');
  const last = confirmed[confirmed.length - 1];
  const lastFrameEnd = T0 + (N - 1) * 2 * FRAME_MS + FRAME_MS;
  ok(!!last && Math.abs(last.end - lastFrameEnd) < 1, `the turn's final segment ends where its last frame ends (+${((lastFrameEnd - T0) / 1000).toFixed(1)} s)`);
  ok(confirmed.every((s, i) => i === 0 || s.start >= confirmed[i - 1].start), 'segment times stay in capture order');
  mgr.removeAll();
}

// ── B: contiguous frames (nothing lost) — times are exactly the gapless ones, as before ────────
{
  const { mgr, SID, confirmed } = run(FRAME_MS);
  const a = [{ text: 'alpha beta', start: 0, end: 6 }, { text: 'gamma delta', start: 12, end: 20 }, { text: 'eps', start: 20, end: 25.6 }];
  const b = [a[0], a[1], { text: 'eps zeta', start: 20, end: 25.6 }];
  mgr.handleTranscriptionResult(SID, 'alpha beta gamma delta eps', 25.6, a, 'en');
  mgr.handleTranscriptionResult(SID, 'alpha beta gamma delta eps zeta', 25.6, b, 'en');
  const g = confirmed.find((s) => s.text === 'gamma delta');
  ok(!!g && Math.abs(g.start - (T0 + 12_000)) < 1 && Math.abs(mgr.getBufferStartMs(SID) - (T0 + 20_000)) < 1,
    'with no lost callbacks the stamps reproduce the gapless timeline (+12.0 s, retained +20.0 s)');
  mgr.removeAll();
}

console.log(`\n✅ capture-timeline: ${checks} checks passed`);
