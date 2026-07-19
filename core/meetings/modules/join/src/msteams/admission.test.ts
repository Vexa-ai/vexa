/**
 * #806 — Teams admission verdicts are TYPED (AdmissionError), not plain Errors.
 *
 * Teams had TWO layers of the same defect: (1) every denial/timeout threw a bare Error (the
 * denial message was byte-identical to the Google Meet path that throws a typed one), and
 * (2) even a typed throw would have been FLATTENED by the function's outer catch, which
 * re-wraps everything into `new Error("Bot was not admitted … ${message}")`. Either layer
 * alone turns a PERMANENT host denial into a transient `join_failure` the control plane
 * re-spawns 3× on the user's quota.
 *
 * Drives the SHIPPED waitForTeamsMeetingAdmission over a fabricated locator-only Page (the
 * Teams checks are all selector-visibility reads — same no-browser pattern as the jitsi and
 * zoom admission tests). Escalation note: cases conclude before any checkEscalation threshold
 * (see zoom/admission.test.ts) so the 5-minute escalation extension never latches.
 *
 * Run: npx tsx src/msteams/admission.test.ts
 */

import { waitForTeamsMeetingAdmission } from "./admission";
import { AdmissionError } from "../shared/admission";
import { resetEscalation } from "../shared/escalation";

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ""}`); failed++; }
}

/** A page whose world is a set of currently-visible selectors, mutable mid-test. */
function makePage(isVisible: (sel: string) => boolean): any {
  const locator = (sel: string): any => ({
    first: () => locator(sel),
    isVisible: async () => isVisible(sel),
    click: async () => {},
    count: async () => 0,
  });
  return {
    locator,
    getByRole: (_role: string, _opts?: any) => locator(`role:${_role}`),
    waitForTimeout: async (_ms: number) => {},
    evaluate: async (fn: any, arg?: any) => {
      (globalThis as any).document = { body: { innerText: "" } };
      try { return fn(arg); } finally { delete (globalThis as any).document; }
    },
  };
}

const LOBBY = "Someone will let you in shortly";
const DENIED = "Sorry, but you were denied";

async function outcomeOf(run: Promise<unknown>): Promise<string> {
  try { await run; return "resolved"; }
  catch (e: any) { return e instanceof AdmissionError ? `AdmissionError:${e.outcome}` : `Error:${e.message}`; }
}

async function main() {
  const cfg: any = {};

  console.log("\n=== host denial → typed PERMANENT denial (survives the outer catch) ===");
  {
    resetEscalation();
    // Start in the lobby; after a few polls the lobby disappears and the denial text renders —
    // the shipped flow then runs checkForTeamsRejection and must throw the TYPED verdict.
    let polls = 0;
    const page = makePage((sel) => {
      const denied = polls > 6;
      if (sel.includes(LOBBY)) { polls++; return !denied; }
      if (sel.includes(DENIED)) return denied;
      return false;
    });
    const got = await outcomeOf(waitForTeamsMeetingAdmission(page, 60_000, cfg));
    check("denial → AdmissionError('denial') — permanent, never re-spawned",
      got === "AdmissionError:denial", got);
  }

  console.log("\n=== lobby forever → typed TRANSIENT lobby_timeout ===");
  {
    resetEscalation();
    // timeout=0: the admission poll loop is never entered; the final still-in-lobby check must
    // throw the typed timeout, not the old plain Error the outer catch then re-wrapped.
    const page = makePage((sel) => sel.includes(LOBBY));
    const got = await outcomeOf(waitForTeamsMeetingAdmission(page, 0, cfg));
    check("lobby timeout → AdmissionError('lobby_timeout') — the legit retry stays a retry",
      got === "AdmissionError:lobby_timeout", got);
  }

  console.log("\n=== an UNTYPED failure still reads as a plain Error (no over-typing) ===");
  {
    resetEscalation();
    // Nothing visible at all: no lobby, no denial, no admission indicators. That is genuinely
    // indeterminate — it must NOT be dressed up as a typed admission verdict.
    const page = makePage(() => false);
    const got = await outcomeOf(waitForTeamsMeetingAdmission(page, 0, cfg));
    check("indeterminate state stays a plain Error (transient join_failure at the driver)",
      got.startsWith("Error:"), got);
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
