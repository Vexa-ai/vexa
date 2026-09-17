// BOT_FAKE_VIDEO_FILE / BOT_CAMERA_ENABLED — a bot can broadcast a still card (identity +
// a recording notice) instead of an empty feed. Pure unit checks, no browser.
//
// The load-bearing case is #3: --use-file-for-fake-video-capture must appear EXACTLY ONCE on
// a launched command line. A duplicate is invisible in either source file and only shows up
// on a running bot's /proc/<pid>/cmdline, so it is asserted here instead.
import { getJoinBrowserArgs, resolveFakeVideoFile, resolveCameraEnabled } from "../browser-args";
import {
  googleCameraOnIndicatorSelector,
  googleCameraOffIndicatorSelector,
} from "../googlemeet/selectors";

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  \x1b[32mPASS\x1b[0m  ${msg}`); }
  else { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${msg}`); }
}

const countFlag = (args: string[], flag: string): number =>
  args.filter((a) => a === flag || a.startsWith(`${flag}=`)).length;

console.log("\n=== 1. Default: an empty feed, and no fake device ===");
{
  delete process.env.BOT_FAKE_VIDEO_FILE;
  delete process.env.BOT_CAMERA_ENABLED;
  assert(resolveFakeVideoFile() === "/dev/null", "resolveFakeVideoFile() defaults to /dev/null");
  assert(resolveCameraEnabled() === false, "resolveCameraEnabled() defaults to false");
  const args = getJoinBrowserArgs();
  assert(
    args.includes("--use-file-for-fake-video-capture=/dev/null"),
    "the launch args carry the empty feed by default",
  );
  assert(
    !args.includes("--use-fake-device-for-media-stream"),
    "no fake DEVICE by default — the flag also replaces the microphone with a beep",
  );
}

console.log("\n=== 2. Negative control: the knobs really drive it (A2) ===");
{
  process.env.BOT_FAKE_VIDEO_FILE = "/opt/card.y4m";
  process.env.BOT_CAMERA_ENABLED = "1";
  assert(resolveFakeVideoFile() === "/opt/card.y4m", "BOT_FAKE_VIDEO_FILE is honoured");
  assert(resolveCameraEnabled() === true, "BOT_CAMERA_ENABLED=1 is honoured");
  const args = getJoinBrowserArgs();
  assert(
    args.includes("--use-file-for-fake-video-capture=/opt/card.y4m"),
    "the file follows the knob — the knob is not a no-op",
  );
  assert(
    args.includes("--use-fake-device-for-media-stream"),
    "the fake device ships WITH a file: without it Chromium enumerates no videoinput at all",
  );
}

console.log("\n=== 3. One flag, one owner: never duplicated ===");
{
  process.env.BOT_FAKE_VIDEO_FILE = "/opt/card.y4m";
  const args = getJoinBrowserArgs();
  assert(
    countFlag(args, "--use-file-for-fake-video-capture") === 1,
    "--use-file-for-fake-video-capture appears exactly once",
  );
  assert(
    countFlag(args, "--use-fake-device-for-media-stream") === 1,
    "--use-fake-device-for-media-stream appears exactly once",
  );
  delete process.env.BOT_FAKE_VIDEO_FILE;
  delete process.env.BOT_CAMERA_ENABLED;
}

console.log("\n=== 4. BOT_CAMERA_ENABLED accepts the shapes a deployment writes ===");
{
  for (const [v, want] of [["1", true], ["true", true], ["TRUE", true], ["0", false], ["", false], ["no", false]] as const) {
    process.env.BOT_CAMERA_ENABLED = v;
    assert(resolveCameraEnabled() === want, `BOT_CAMERA_ENABLED=${JSON.stringify(v)} -> ${want}`);
  }
  delete process.env.BOT_CAMERA_ENABLED;
}

console.log("\n=== 5. The camera toggle's two states are distinguishable ===");
{
  // Meet's aria-label names the ACTION, not the state, so the two selectors must not
  // overlap — otherwise a caller cannot drive the toggle to a wanted state.
  assert(
    googleCameraOnIndicatorSelector !== googleCameraOffIndicatorSelector,
    "the on-state and off-state selectors are distinct",
  );
  assert(
    googleCameraOnIndicatorSelector.includes("Turn off camera"),
    "the ON indicator matches the control offered while the camera is on",
  );
  assert(
    googleCameraOffIndicatorSelector.includes("Turn on camera"),
    "the OFF indicator matches the control offered while the camera is off",
  );
}

console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);
