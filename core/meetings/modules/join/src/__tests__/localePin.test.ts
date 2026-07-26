// #856 — the browser UI locale is a PINNED input, not an uncontrolled one.
// Pure unit checks, no browser: the launch flags carry --lang / --accept-lang for
// the resolved BOT_UI_LOCALE knob, and the constructed Meet URL carries ?hl=.
import { getJoinBrowserArgs, getLocaleBrowserArgs, resolveBotUiLocale } from "../browser-args";
import { withPinnedMeetLocale } from "../googlemeet/join";

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  \x1b[32mPASS\x1b[0m  ${msg}`); }
  else { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${msg}`); }
}

console.log("\n=== 1. Default pin: en-US ===");
{
  delete process.env.BOT_UI_LOCALE;
  assert(resolveBotUiLocale() === "en-US", "resolveBotUiLocale() defaults to en-US when the knob is unset");
  const args = getJoinBrowserArgs();
  assert(args.includes("--lang=en-US"), "getJoinBrowserArgs() carries --lang=en-US by default");
  assert(args.includes("--accept-lang=en-US,en"), "getJoinBrowserArgs() carries --accept-lang=en-US,en by default");
}

console.log("\n=== 2. Negative control: the knob really drives it (A2) ===");
{
  process.env.BOT_UI_LOCALE = "hu-HU";
  assert(resolveBotUiLocale() === "hu-HU", "BOT_UI_LOCALE=hu-HU is honoured");
  const args = getLocaleBrowserArgs();
  assert(args.includes("--lang=hu-HU"), "--lang follows the knob (hu-HU) — the pin is not a no-op");
  assert(args.includes("--accept-lang=hu-HU,hu"), "--accept-lang follows the knob (hu-HU,hu)");
  delete process.env.BOT_UI_LOCALE;
}

console.log("\n=== 3. ?hl= on the constructed Meet URL (BOT_AUTHENTICATED lever) ===");
{
  const url = withPinnedMeetLocale("https://meet.google.com/abc-defg-hij", "en-US");
  assert(/[?&]hl=en(&|$)/.test(url), `?hl=en appended (bare language subtag): ${url}`);

  const hu = withPinnedMeetLocale("https://meet.google.com/abc-defg-hij", "hu-HU");
  assert(/[?&]hl=hu(&|$)/.test(hu), `?hl follows the pinned locale: ${hu}`);

  const preset = withPinnedMeetLocale("https://meet.google.com/abc-defg-hij?hl=fr", "en-US");
  assert(/hl=fr/.test(preset) && !/hl=en/.test(preset), "an explicit caller hl= is preserved, never overridden");

  const bad = withPinnedMeetLocale("not a url", "en-US");
  assert(bad === "not a url", "a malformed URL is returned unchanged (no throw)");
}

console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);
