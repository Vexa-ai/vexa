/**
 * Capture silence hangover — pure producer-boundary policy.
 *
 * A loud frame opens a bounded allowance for following silent frames. This
 * preserves real inter-phrase pauses in the existing PCM stream without
 * forwarding an unbounded idle stream.
 */
import { advanceSilenceGate } from "./gmeet-capture.js";

let failed = 0;
const check = (name: string, cond: boolean) => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}`);
  if (!cond) failed++;
};

const FRAME_SAMPLES = 4096;
const HANGOVER_SAMPLES = 32000;

let state = advanceSilenceGate(false, FRAME_SAMPLES, 0, HANGOVER_SAMPLES);
check("idle silence stays gated", state.emit === false && state.remainingSamples === 0);

state = advanceSilenceGate(true, FRAME_SAMPLES, state.remainingSamples, HANGOVER_SAMPLES);
check("speech emits and opens the full hangover", state.emit === true && state.remainingSamples === HANGOVER_SAMPLES);

let silentFramesEmitted = 0;
while (state.remainingSamples > 0) {
  state = advanceSilenceGate(false, FRAME_SAMPLES, state.remainingSamples, HANGOVER_SAMPLES);
  if (state.emit) silentFramesEmitted++;
}
check(
  "two-second hangover preserves exactly the bounded whole-frame allowance",
  silentFramesEmitted === Math.ceil(HANGOVER_SAMPLES / FRAME_SAMPLES),
);

state = advanceSilenceGate(false, FRAME_SAMPLES, state.remainingSamples, HANGOVER_SAMPLES);
check("silence after the allowance is gated again", state.emit === false && state.remainingSamples === 0);

state = advanceSilenceGate(true, FRAME_SAMPLES, state.remainingSamples, HANGOVER_SAMPLES);
state = advanceSilenceGate(false, FRAME_SAMPLES, state.remainingSamples, HANGOVER_SAMPLES);
state = advanceSilenceGate(true, FRAME_SAMPLES, state.remainingSamples, HANGOVER_SAMPLES);
check("resumed speech resets the full allowance", state.emit === true && state.remainingSamples === HANGOVER_SAMPLES);

if (failed) {
  console.error(`\n❌ silence-hangover: ${failed} checks FAILED.`);
  process.exit(1);
}
console.log("\n✅ silence-hangover: bounded pause PCM is preserved without unbounded idle audio.");
