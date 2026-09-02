/** Claim the administrator role from an EXISTING session — the way back out of a dead end.
 *
 *  ⚠ THE DEAD END THIS EXISTS TO OPEN (observed live 2026-09-02, 08:48Z). The admin role was only
 *  ever claimable inside `findOrCreateUserToken`, i.e. only while walking through a sign-in door.
 *  An instance that acquired live sessions BEFORE the company-layer gate shipped therefore had no
 *  reachable claim at all: the founder's browser held a valid cookie minted months earlier,
 *  `admin_exists` was false, and a cookie never traverses a sign-in door twice. The screen said
 *  "this instance is not set up" and nothing in the product could set it up. This route is the
 *  missing edge — the same claim, reachable by somebody who is already in.
 *
 *  IDENTITY. The `vexa-token` cookie is the ONLY input, and it is validated through admin-api's
 *  internal oracle before it means anything. The `vexa-user-info` cookie is display-only and MUST
 *  NOT be read here: httpOnly stops a script reading it, not a hand-crafted Cookie header, so a
 *  claim that trusted it would let anyone who can type a curl command become the administrator of
 *  a fresh instance. This is the single highest-value privilege the product hands out; it is worth
 *  saying out loud that the cheap cookie is not allowed to grant it.
 *
 *  FAIL CLOSED, deliberately, and note that this is the OPPOSITE direction from the rest of the
 *  gate. `instanceState()` fails towards "the gate is down" because guessing wrong there locks
 *  everybody out of a working instance. Here, guessing wrong GRANTS ADMIN. So an unreachable probe
 *  refuses: the cost of refusing is that the admin presses the button again in ten seconds, and the
 *  cost of allowing is that a stranger becomes the administrator during an outage.
 *
 *  RACES. admin-api serialises concurrent claims under an advisory lock and is a no-op once an
 *  admin exists, so two tabs pressing the button together are safe — one claims, the other is told
 *  an admin now exists. The `admin_exists` check below is therefore a courtesy for the message, not
 *  the safety property; the safety property is upstream and cannot be raced from here.
 */
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { AUTH_COOKIE, claimAdminRole, instanceState, validateAuthToken } from "../adminApi";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

export async function POST() {
  const token = (await cookies()).get(AUTH_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ error: "Not signed in" }, { status: 401, headers: NO_STORE });
  }

  // The cookie is a claim of identity, not identity. Only the oracle's answer counts.
  const who = await validateAuthToken(token);
  if (!who.ok) {
    return NextResponse.json(
      { error: who.status === 401 ? "Not signed in" : "Could not verify your session — try again in a moment." },
      { status: who.status === 401 ? 401 : 503, headers: NO_STORE },
    );
  }

  // Already claimed? Say so plainly and tell the client to reload — the page it is showing (a claim
  // screen) is now stale, and reloading is what puts the right screen in front of the user.
  const state = await instanceState();
  if (state.admin_exists) {
    return NextResponse.json(
      { error: "This instance already has an administrator.", admin_exists: true, reload: true },
      { status: 409, headers: NO_STORE },
    );
  }

  const claimed = await claimAdminRole(who.userId);
  if (!claimed.ok) {
    console.error(`[terminal-auth] admin claim failed for user ${who.userId}: ${claimed.error}`);
    return NextResponse.json(
      { error: "Could not claim this instance — try again in a moment." },
      { status: claimed.status >= 400 && claimed.status < 600 ? claimed.status : 503, headers: NO_STORE },
    );
  }

  // `claimed:false` means admin-api's lock handed the role to somebody else between our check and
  // our write. That is not an error for this user's PAGE — an admin exists either way and the
  // reload puts them on the correct screen — but it is not the same event, so it is not reported
  // as one.
  console.info(`[terminal-auth] admin claim by user ${who.userId} (${who.email}): claimed=${claimed.claimed}`);
  return NextResponse.json(
    { success: true, claimed: claimed.claimed, email: who.email },
    { headers: NO_STORE },
  );
}
