// #856 — the browser UI locale is PINNED to English, not an uncontrolled input.
// Pure unit checks, no browser: the launch flags carry --lang / --accept-lang for
// en-US, and the constructed Meet URL carries ?hl=.
import { getJoinBrowserArgs, getLocaleBrowserArgs } from "../browser-args";
import { PINNED_UI_LOCALE } from "../shared/ui-locale";
import { withPinnedMeetLocale } from "../googlemeet/join";

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  \x1b[32mPASS\x1b[0m  ${msg}`); }
  else { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${msg}`); }
}

console.log("\n=== 1. The pin: en-US ===");
{
  assert(PINNED_UI_LOCALE === "en-US", "the pinned UI locale is en-US");
  const args = getJoinBrowserArgs();
  assert(args.includes("--lang=en-US"), "getJoinBrowserArgs() carries --lang=en-US");
  assert(args.includes("--accept-lang=en-US,en"), "getJoinBrowserArgs() carries --accept-lang=en-US,en");
  assert(getLocaleBrowserArgs().length === 2, "exactly the two locale flags, no other language source");
}

console.log("\n=== 2. ?hl= on the constructed Meet URL (BOT_AUTHENTICATED lever) ===");
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
