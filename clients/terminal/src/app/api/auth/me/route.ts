/** Who-am-I — the login gate calls this to decide whether to show the sign-in card. The token is
 *  VALIDATED against admin-api's internal oracle: a definitively revoked/deleted token (401) clears
 *  the cookies and reports unauthenticated, so a server-side user wipe actually logs the browser out
 *  and first-run onboarding can reappear (#553 — the session was sticky to cookie PRESENCE before).
 *  Transient oracle failures (admin-api down/unconfigured) keep the session — never flap on a blip. */
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
    return NextResponse.json({ authenticated: false }, { status: 401, headers: NO_STORE });
  }

  const validated = await validateAuthToken(token);
  if (!validated.ok && validated.status === 401) {
    // The token is definitively dead (revoked, or its user was wiped) — fail closed and clear the
    // stale cookies so the client re-enters the sign-in / first-run flow.
    cookieStore.delete(AUTH_COOKIE);
    cookieStore.delete(USER_INFO_COOKIE);
    return NextResponse.json({ authenticated: false }, { status: 401, headers: NO_STORE });
  }

  let email: string | undefined;
  let name: string | undefined;
  if (info) {
    try {
      ({ email, name } = JSON.parse(info) as { email?: string; name?: string });
    } catch {
      /* malformed cookie — still authenticated by the token, just no email to show */
    }
  }

  return NextResponse.json(
    { authenticated: true, user: { email: email ?? null, name: name ?? null } },
    { headers: NO_STORE },
  );
}
