// The join launch flags never turn Chromium's sandbox off: the bot's launch keeps the sandbox on
// wherever it can start (@vexa/remote-browser sandbox.ts). Pure unit check, no browser.
import { JOIN_BROWSER_ARGS, getJoinBrowserArgs } from "../browser-args";

let failed = 0;
for (const off of ["--no-sandbox", "--disable-setuid-sandbox", "--no-zygote"]) {
  for (const [name, args] of [["JOIN_BROWSER_ARGS", JOIN_BROWSER_ARGS], ["getJoinBrowserArgs()", getJoinBrowserArgs()]] as const) {
    if (args.includes(off)) { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${name} turns the sandbox off (${off})`); }
  }
}
if (failed) process.exit(1);
console.log("  \x1b[32mPASS\x1b[0m  the join launch flags leave Chromium's sandbox on");
