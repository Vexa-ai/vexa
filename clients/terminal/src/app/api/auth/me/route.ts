/** Who-am-I — the ONE endpoint the client asks "is this tab still signed in?".
 *
 *  ⚠ WHAT THIS USED TO DO, AND WHY IT WAS A DEFECT (2026-09-01). It read the two cookies and
 *  answered `{authenticated: true}` on the mere PRESENCE of the auth cookie — no backend
 *  round-trip. A cookie is not a session: when a login token was revoked server-side the cookie
 *  stayed exactly where it was, this route kept saying "signed in", the login gate let the whole
 *  shell render, and every real request behind it 401'd. The user saw a working app that could not
 *  do anything. An endpoint whose entire job is answering that question must not answer it from a
 *  value the client can hold after it stopped being true.
 *
 *  It now VALIDATES the token against admin-api's internal oracle — the same
 *  `POST /internal/validate` every proxied route and the gateway itself go through, so this answer
 *  and theirs cannot disagree. As a side effect it stamps the token's `last_used_at`, which is what
 *  the login-token prune reads to know a session is live (see adminApi.ts § pruneLoginTokens).
 *
 *  FAIL DIRECTION. Only a 401 from the oracle — the token genuinely is not accepted — signs anybody
 *  out. If the oracle is unreachable or misconfigured (503/502/network) the caller stays signed in,
 *  flagged `degraded`. This is a liveness probe, not an authorization boundary: every route that
 *  actually does something validates independently and fails closed on its own. Ejecting the user
 *  because admin-api blinked would be a worse failure than the one this fixes.
 *
 *  The `vexa-user-info` cookie remains display-only. When the oracle answers, ITS email wins. */
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { AUTH_COOKIE, USER_INFO_COOKIE, validateAuthToken } from "../adminApi";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

export async function GET() {
  const cookieStore = await cookies();
  const token = cookieStore.get(AUTH_COOKIE)?.value;
  const info = cookieStore.get(USER_INFO_COOKIE)?.value;

  if (!token) {
    return NextResponse.json({ authenticated: false, reason: "no_session" }, { status: 401, headers: NO_STORE });
  }

  // Display-only fallbacks, used when the oracle can't be reached.
  let email: string | undefined;
  let name: string | undefined;
  if (info) {
    try {
      ({ email, name } = JSON.parse(info) as { email?: string; name?: string });
    } catch {
      /* malformed cookie — still authenticated by the token, just no email to show */
    }
  }

  const validated = await validateAuthToken(token);

  if (validated.ok) {
    return NextResponse.json(
      { authenticated: true, user: { email: validated.email ?? email ?? null, name: name ?? null } },
      { headers: NO_STORE },
    );
  }

  // The token was REFUSED — revoked, deleted, or expired. This is the signed-out answer, and the
  // only branch that takes a session away.
  if (validated.status === 401) {
    return NextResponse.json({ authenticated: false, reason: "session_ended" }, { status: 401, headers: NO_STORE });
  }

  // The oracle could not answer (unconfigured / unreachable / timed out). Do NOT sign the user out
  // on an infrastructure blip — say so instead, and let the real routes fail closed if they must.
  console.warn(`[terminal-auth] /api/auth/me could not validate the session (${validated.status}): ${validated.error}`);
  return NextResponse.json(
    { authenticated: true, degraded: true, user: { email: email ?? null, name: name ?? null } },
    { headers: NO_STORE },
  );
}
