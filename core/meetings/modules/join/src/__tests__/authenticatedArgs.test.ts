// #1725 — an authenticated launch must not carry --incognito (it ignores the restored
// profile, so the bot joins signed out); a guest launch keeps the canonical set unchanged.
// Pure unit checks, no browser.
import { getJoinBrowserArgs, getAuthenticatedJoinBrowserArgs } from "../browser-args";

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  \x1b[32mPASS\x1b[0m  ${msg}`); }
  else { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${msg}`); }
}

console.log("\n=== #1725 authenticated join args ===");
{
  const guest = getJoinBrowserArgs();
  const auth = getAuthenticatedJoinBrowserArgs();
  assert(guest.includes("--incognito"), "guest launch keeps --incognito (control: the flag is in the canonical set)");
  assert(!auth.includes("--incognito"), "authenticated launch drops --incognito");
  assert(auth.length === guest.length - 1, "only --incognito is removed");
  assert(guest.filter((a) => a !== "--incognito").every((a, i) => a === auth[i]), "every other flag is kept, in order");
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
