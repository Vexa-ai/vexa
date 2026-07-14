/**
 * #592 — authoritative members-only/lobby detection + typed timeout attribution.
 *
 * A fresh meet.jit.si room is members-only until a moderator arrives; the conference fails with
 * MEMBERS_ONLY and redux records the lobby room JID in features/base/conference.membersOnly. The
 * witnessed silent loop happened when DOM/text scraping missed that state (blank/error render) —
 * so the bot mislabelled a real lobby as "unknown" and escalated down the illegal joining→needs_help
 * path. getLobbyState reads the app's own verdict instead. And a give-up must throw a TYPED
 * AdmissionError('lobby_timeout') so the driver reports awaiting_admission_timeout, not a generic
 * silently-retried join_failure.
 *
 * Drives the SHIPPED getLobbyState + waitForJitsiMeetingAdmission over a fabricated Page/APP stub
 * (same no-browser pattern as the other jitsi tests).
 *
 * Run: npx tsx src/jitsi/admission.test.ts
 */

import { getLobbyState, waitForJitsiMeetingAdmission } from "./admission";
import { AdmissionError } from "../shared/admission";

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ""}`); failed++; }
}

// evaluate runs the probe fn in-node; getLobbyState/getAppJoinedState read globalThis.APP.
const page: any = {
  async evaluate(fn: any, arg?: any) { return fn(arg); },
  async waitForTimeout(_ms: number) {},
};

function setApp(opts: { joined?: boolean; membersOnly?: string | null; knocking?: boolean }) {
  (globalThis as any).APP = {
    conference: { isJoined: () => !!opts.joined },
    store: { getState: () => ({
      "features/base/conference": { membersOnly: opts.membersOnly ?? undefined },
      "features/lobby": { knocking: !!opts.knocking },
    }) },
  };
}

async function main() {
  console.log("\n=== getLobbyState — the app's own members-only verdict ===");

  setApp({ membersOnly: "room@lobby.meet.jit.si" });
  check("members-only room JID in redux → 'lobby'", (await getLobbyState(page)) === "lobby");

  setApp({ knocking: true });
  check("explicit Lobby knocking → 'lobby'", (await getLobbyState(page)) === "lobby");

  setApp({ membersOnly: null, knocking: false });
  check("open conference → 'not-lobby'", (await getLobbyState(page)) === "not-lobby");

  delete (globalThis as any).APP;
  check("APP/redux absent → 'no-api'", (await getLobbyState(page)) === "no-api");

  console.log("\n=== waitForJitsiMeetingAdmission — timeout is typed (lobby_timeout) ===");

  // Not joined + members-only lobby + zero timeout → the wait gives up immediately and must throw
  // a TYPED AdmissionError('lobby_timeout'), not a bare Error (which the driver would collapse to a
  // silently-retried join_failure). timeoutMs=0 exits before any escalation fires.
  setApp({ joined: false, membersOnly: "room@lobby.meet.jit.si" });
  let caught: any = null;
  try { await waitForJitsiMeetingAdmission(page, 0, {} as any); }
  catch (e) { caught = e; }
  check("timeout throws AdmissionError", caught instanceof AdmissionError, `got ${caught && caught.name}`);
  check("outcome = 'lobby_timeout'",
    caught instanceof AdmissionError && caught.outcome === "lobby_timeout",
    caught instanceof AdmissionError ? caught.outcome : "n/a");

  delete (globalThis as any).APP;
  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
