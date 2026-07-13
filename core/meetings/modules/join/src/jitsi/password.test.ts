/**
 * Late-arriving room-password dialog (TAKE finding on #543): the prompt is
 * delivered over the XMPP round-trip and can land AFTER join.ts's early check,
 * during the admission wait. These tests drive the SHIPPED
 * `fillPasswordPromptIfPresent` + `waitForJitsiMeetingAdmission` over a
 * fabricated Page stub (same pattern as the other jitsi tests: no browser).
 *
 * Run: npx tsx src/jitsi/password.test.ts
 */

import { fillPasswordPromptIfPresent } from "./password";
import { waitForJitsiMeetingAdmission } from "./admission";

let passed = 0;
let failed = 0;

function check(name: string, ok: boolean, detail?: string) {
  if (ok) {
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
  } else {
    console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ""}`);
    failed++;
  }
}

/**
 * A fabricated Playwright Page. Controls:
 *  - `dialogVisibleAfterPolls`: the password dialog becomes visible only after
 *    N waitForTimeout ticks (simulating the XMPP-delayed prompt).
 *  - filling + Enter marks the room joined (APP.conference.isJoined → true) and
 *    hides the dialog — the real deployment behavior on a correct passcode.
 */
function makePageStub(opts: { dialogVisibleAfterPolls: number }) {
  const state = {
    ticks: 0,
    filled: [] as string[],
    enters: 0,
    joined: false,
    dialogUp: () => !state.joined && state.ticks >= opts.dialogVisibleAfterPolls,
  };
  const pwLocator = {
    first() { return this; },
    async isVisible(_o?: any) { return state.dialogUp(); },
    async fill(v: string) { state.filled.push(v); },
    async waitFor(_o?: any) { if (!state.dialogUp()) throw new Error("timeout"); },
  };
  const hiddenLocator = {
    first() { return this; },
    async isVisible(_o?: any) { return false; },
    async waitFor(_o?: any) { throw new Error("timeout"); },
  };
  const page: any = {
    locator(sel: string) {
      return sel.includes('type="password"') ? pwLocator : hiddenLocator;
    },
    keyboard: {
      async press(key: string) {
        if (key === "Enter" && state.filled.length > 0) {
          state.enters++;
          state.joined = true; // correct passcode → conference goes live
        }
      },
    },
    async waitForTimeout(_ms: number) { state.ticks++; },
    // evaluate runs the probe fn in-node; getAppJoinedState reads globalThis.APP.
    async evaluate(fn: any, arg?: any) { return fn(arg); },
  };
  return { page, state };
}

async function main() {
  console.log("\n=== fillPasswordPromptIfPresent — idempotent instant check ===");

  {
    const { page, state } = makePageStub({ dialogVisibleAfterPolls: 2 });
    (globalThis as any).APP = { conference: { isJoined: () => state.joined } };
    const cfg: any = { passcode: "s3cret" };

    check("dialog absent → 'absent', nothing filled",
      (await fillPasswordPromptIfPresent(page, cfg)) === "absent" && state.filled.length === 0);

    state.ticks = 2; // dialog now up
    check("dialog present → fills passcode and submits",
      (await fillPasswordPromptIfPresent(page, cfg)) === "submitted"
        && state.filled.join(",") === "s3cret" && state.enters === 1);

    state.joined = false; state.ticks = 3; // dialog visible again (wrong-password shape)
    check("second call does NOT resubmit (idempotent per page)",
      (await fillPasswordPromptIfPresent(page, cfg)) === "already-submitted"
        && state.filled.length === 1);
  }

  {
    const { page, state } = makePageStub({ dialogVisibleAfterPolls: 0 });
    (globalThis as any).APP = { conference: { isJoined: () => state.joined } };
    let threw = "";
    try { await fillPasswordPromptIfPresent(page, { passcode: "" } as any); }
    catch (e: any) { threw = String(e.message); }
    check("dialog present without passcode → throws password_required",
      threw.includes("password_required"));
  }

  console.log("\n=== waitForJitsiMeetingAdmission — late dialog answered in the poll loop ===");

  {
    // Dialog appears only after a few poll ticks — i.e. AFTER join.ts's early
    // window. Pre-fix, the loop never touched it and the bot sat to timeout.
    const { page, state } = makePageStub({ dialogVisibleAfterPolls: 4 });
    (globalThis as any).APP = { conference: { isJoined: () => state.joined } };
    const cfg: any = { passcode: "room-pass" };

    let admitted = false; let err = "";
    try { admitted = await waitForJitsiMeetingAdmission(page, 30000, cfg); }
    catch (e: any) { err = String(e.message); }

    check("admission loop fills the late-appearing password dialog and admits",
      admitted === true && state.filled.join(",") === "room-pass" && state.enters === 1,
      err || `filled=${JSON.stringify(state.filled)}`);
  }

  {
    // Late dialog + NO passcode → the loop fails fast with the structured
    // reason instead of waiting out the full admission timeout.
    const { page, state } = makePageStub({ dialogVisibleAfterPolls: 3 });
    (globalThis as any).APP = { conference: { isJoined: () => state.joined } };
    let err = "";
    try { await waitForJitsiMeetingAdmission(page, 30000, { passcode: "" } as any); }
    catch (e: any) { err = String(e.message); }
    check("late dialog without passcode → fail fast with password_required",
      err.includes("password_required"));
  }

  delete (globalThis as any).APP;
  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
