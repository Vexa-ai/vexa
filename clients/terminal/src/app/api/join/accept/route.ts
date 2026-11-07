/** `POST /api/join/accept` — the one click that signs an invited person in AND joins them (PRD
 *  0.13.4 S2, E1).
 *
 *  The invite mail carries one link, `/join?i=<token>`. Opening it — by a person, a Teams preview,
 *  Defender Safe Links, any mail scanner — is a READ: the join page shows who invited them, to which
 *  workspace or room, as what, and one button. Only this POST, from that button on this instance's
 *  own page, changes anything:
 *
 *    0. same-origin only (`../../auth/sameOrigin.ts`), and counted per client address (M6);
 *    1. agent-api spends the invite's SIGN-IN half, once (`/internal/invites/signin-claim`). It
 *       answers only for a restricted invite to exactly one address that this deployment MAILED to
 *       that address — the mailbox is the proof, as for the emailed sign-in link — and only inside
 *       its window. A link handed to the inviter never signs anybody in;
 *    2. admin-api admits the address (`via_invite`: a mailed invite is a way in, unless the operator
 *       closed it) and the account is found or created — the same `findOrCreateUserToken` every door
 *       uses;
 *    3. the invite is accepted AS that person, through the gateway's ordinary authenticated door, so
 *       agent-api checks their verified address itself and joins the room the invite named;
 *    4. 303 to the room (`/r/chat/<id>`) or the workspace (`/w/<id>`), with the session cookies.
 *
 *  Every refusal is a 303 back to the join page with a note (`n=`): a link already spent, out of its
 *  window, or not a sign-in link (`signin`, and the page offers the ordinary emailed sign-in link), a
 *  cross-site post (`crosssite`), too many attempts (`rate`), an address the instance refuses
 *  (`notallowed`), or a service that could not answer (`unavailable`). Never a dead end, and no page
 *  of its own. GET and HEAD never act.
 */
import { NextResponse, type NextRequest } from "next/server";
import { AUTH_COOKIE, USER_INFO_COOKIE, acceptInviteAs, claimInviteSignin, findOrCreateUserToken } from "../../auth/adminApi";
import { CLIENT_ADDRESS_HEADER } from "../../auth/clientAddress.mjs";
import { takeJoinRequest } from "../../auth/linkRateLimit";
import { signinMethodEnabled } from "../../auth/oidcConfig.mjs";
import { isSecureRequest, sameOriginPost } from "../../auth/sameOrigin";
import { joinPage, roomOrWorkspacePath as landing } from "../../../join/joinState";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

function to(location: string): NextResponse {
  return new NextResponse(null, { status: 303, headers: { Location: location, ...NO_STORE } });
}

/** Opening the action URL itself is a read: back to the page that asks. */
export async function GET(request: NextRequest) {
  return to(joinPage(request.nextUrl.searchParams.get("i") || ""));
}

export async function HEAD() {
  return new NextResponse(null, { status: 200, headers: NO_STORE });
}

export async function POST(request: NextRequest) {
  let token = "";
  try {
    const form = await request.formData();
    const t = form.get("i");
    token = typeof t === "string" ? t.trim() : "";
  } catch { /* not a form body */ }
  if (!token) return to("/join");
  if (!sameOriginPost(request)) {
    console.info("[terminal-join] one-click join refused: cross-site POST");
    return to(joinPage(token, "crosssite"));
  }
  if (!(await takeJoinRequest(request.headers.get(CLIENT_ADDRESS_HEADER) || "unknown"))) {
    return to(joinPage(token, "rate"));
  }
  // The operator closed the emailed door (VEXA_SIGNIN_METHODS without `email`): a mailbox-proof
  // sign-in is that door, so the invite falls back to the instance's other ways in.
  if (!signinMethodEnabled("email")) return to(joinPage(token, "signin"));

  const claim = await claimInviteSignin(token);
  if (!claim.ok) {
    // used · out of its window · never a sign-in link · unknown · unavailable — the page explains
    // and offers the ordinary emailed sign-in link. Nothing was created.
    return to(joinPage(token, claim.reason === "unavailable" ? "unavailable" : "signin"));
  }
  const result = await findOrCreateUserToken(claim.email, { emailVerifiedVia: "invite_link", viaInvite: true });
  if (!result.ok) {
    if (result.refused === "not-allowed") {
      console.info(`[terminal-join] one-click join refused: ${claim.email} may not sign in`);
      return to(joinPage(token, "notallowed"));
    }
    console.error(`[terminal-join] one-click join could not sign in: ${result.error}`);
    return to(joinPage(token, "unavailable"));
  }
  const { user, token: apiToken } = result;
  const joined = await acceptInviteAs(apiToken, token);
  // Signed in either way. A join that did not finish lands on the invite page, which now shows the
  // signed-in confirm card with its Join button.
  const location = joined.ok ? landing(joined.workspaceId || claim.workspaceId, joined.room ?? claim.room) : joinPage(token);
  const res = to(location);
  const opts = { httpOnly: true, secure: isSecureRequest(), sameSite: "lax" as const, maxAge: 60 * 60 * 24 * 30, path: "/" };
  res.cookies.set(AUTH_COOKIE, apiToken, opts);
  res.cookies.set(USER_INFO_COOKIE, JSON.stringify({ id: user.id, email: user.email, name: user.name || user.email.split("@")[0] }), opts);
  return res;
}
