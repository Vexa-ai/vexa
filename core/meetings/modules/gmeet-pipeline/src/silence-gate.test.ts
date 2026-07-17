/**
 * #617 · A2 — near-silent windows are never submitted to Whisper (the SOURCE of the silence
 * "YouTube-outro" hallucinations reported in #613), and the rms() oracle draws the boundary.
 * Run: npx tsx src/silence-gate.test.ts  (or via `npm test`, chained).
 */
import { SpeakerStreamManager, rms } from "./speaker-streams.js";

let failed = 0;
const check = (name: string, cond: boolean) => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}`);
  if (!cond) failed++;
};

const SR = 16000;
const silent = new Float32Array(SR * 2); // 2s digital silence → RMS 0
const speech = new Float32Array(SR * 2).fill(0.1); // 2s well above the 0.0025 threshold

// The rms() oracle.
check("rms(silence) === 0", rms(silent) === 0);
check("rms(speech) ≈ 0.1 (>> threshold)", Math.abs(rms(speech) - 0.1) < 1e-6);
check("rms(empty) === 0", rms(new Float32Array(0)) === 0);

// The gate: drive one unconfirmed window through the real submit path (flushSpeaker → submitBuffer)
// and count how many times it reaches Whisper (onSegmentReady).
async function submitsFor(audio: Float32Array): Promise<number> {
  const mgr = new SpeakerStreamManager({ silenceRmsThreshold: 0.0025 });
  let submits = 0;
  mgr.onSegmentReady = () => { submits++; };
  mgr.addSpeaker("s1", "Alice");
  mgr.feedAudio("s1", audio);
  await mgr.flushSpeaker("s1", true);
  mgr.removeSpeaker("s1"); // clear the auto-submit interval so the process exits + count is exact
  return submits;
}

check("near-silent window is NOT submitted (RED before the gate: it was)", (await submitsFor(silent)) === 0);
check("real-speech window IS submitted (no over-suppression)", (await submitsFor(speech)) === 1);

if (failed) { console.error(`\n❌ silence-gate: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ silence-gate: near-silent skipped, speech submitted, rms oracle correct.`);
