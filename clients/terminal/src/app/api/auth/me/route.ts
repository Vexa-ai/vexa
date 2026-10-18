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
 *  The `vexa-user-info` cookie remains display-only. When the oracle answers, ITS email wins.
 *
 *  `is_admin` (added 2026-09-02) rides along because the client decides admin-only affordances
 *  (e.g. aiming a chat at the company layer) on every page load, and this route already has the
 *  fact in hand: whether the validated subject is this instance's administrator. It comes from the
 *  oracle, never from the info cookie.
 *
 *  ⚠ IT IS THREE-VALUED ON PURPOSE: true, false, or NULL when the oracle could not be reached
 *  (the `degraded` branch below). A degraded probe knows nothing about who this is, and collapsing
 *  that to `false` would show the admin a "you are not the administrator" refusal because admin-api
 *  blinked — the exact class of failure the fail-direction comment above exists to prevent. The
 *  consumer refuses only on a positive `false`. */
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { AUTH_COOKIE, USER_INFO_COOKIE, validateAuthToken } from "../adminApi";
import { PERSON_COOKIE, PERSON_INFO_COOKIE, adminAccountAddresses } from "../companyMode";
import { testUsersEnabled } from "../testUsers";
import { sessionCookieHeader, upgradeBrowserSession } from "../sessionUpgrade";

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

  // THE COMPANY PROFILE (ADR-0049). While this tab works as the company the auth cookie carries the
  // admin account's session and the person's own token waits in PERSON_COOKIE. `company.person` is
  // display-only (who to switch back to); `can_work_as_company` is an instance administrator on a
  // deployment that names an admin account. Neither authorizes anything — company/ re-checks both.
  const personToken = cookieStore.get(PERSON_COOKIE)?.value;
  let person: { email?: string; name?: string } | null = null;
  if (personToken) {
    try { person = JSON.parse(cookieStore.get(PERSON_INFO_COOKIE)?.value || "{}"); } catch { person = {}; }
  }

  if (validated.ok) {
    // A browser signed in before sessions existed still holds the old login KEY (hotfix 2026-10-18):
    // move it onto a session now, on the page load, so its first card press already works. Best-effort
    // — a refusal leaves the tab exactly as signed in as it was. Never in a Company-mode tab.
    const upgraded = validated.credentialClass === "key" && !personToken ? await upgradeBrowserSession() : null;
    const res = NextResponse.json(
      {
        authenticated: true,
        // From the oracle, not the cookie — see the header note on why this is three-valued.
        is_admin: validated.isAdmin,
        // THE ADMIN ACCOUNT (identity.v1 `admin_account`): the one account that works on the company
        // layer, and on nothing else. The client shows it only Company, and shows Company to nobody
        // else. Same three values as `is_admin`.
        admin_account: validated.adminAccount,
        // The account id scopes this browser's stored state to this account (platform/accountStorage).
        user: { id: String(validated.userId), email: validated.email ?? email ?? null, name: name ?? null },
        company: personToken && validated.adminAccount ? { person: { email: person?.email ?? null, name: person?.name ?? null } } : null,
        can_work_as_company: validated.isAdmin && !validated.adminAccount && !personToken && adminAccountAddresses().length > 0,
        // app.dev tooling (#1783): "Test users…" in the account menu — switched on AND an administrator
        dev_test_users: testUsersEnabled() && validated.isAdmin === true,
      },
      { headers: NO_STORE },
    );
    if (upgraded) res.headers.append("Set-Cookie", sessionCookieHeader(upgraded));
    return res;
  }

  // The token was REFUSED — revoked, deleted, or expired. This is the signed-out answer, and the
  // only branch that takes a session away.
  if (validated.status === 401) {
    // A Company session that ran out puts the person back in their own session, not on sign-in.
    if (personToken && (await validateAuthToken(personToken)).ok) {
      const res = NextResponse.json({ authenticated: false, reason: "company_session_ended" }, { status: 401, headers: NO_STORE });
      const secure = (process.env.TERMINAL_URL || "").startsWith("https://") || (process.env.NEXTAUTH_URL || "").startsWith("https://");
      const opts = { httpOnly: true, secure, sameSite: "lax" as const, path: "/", maxAge: 60 * 60 * 24 * 30 };
      res.cookies.set(AUTH_COOKIE, personToken, opts);
      const info = cookieStore.get(PERSON_INFO_COOKIE)?.value;
      if (info) res.cookies.set(USER_INFO_COOKIE, info, opts);
      res.cookies.set(PERSON_COOKIE, "", { ...opts, maxAge: 0 });
      res.cookies.set(PERSON_INFO_COOKIE, "", { ...opts, maxAge: 0 });
      return res;
    }
    return NextResponse.json({ authenticated: false, reason: "session_ended" }, { status: 401, headers: NO_STORE });
  }

  // The oracle could not answer (unconfigured / unreachable / timed out). Do NOT sign the user out
  // on an infrastructure blip — say so instead, and let the real routes fail closed if they must.
  console.warn(`[terminal-auth] /api/auth/me could not validate the session (${validated.status}): ${validated.error}`);
  return NextResponse.json(
    // is_admin: null — "unknown", NOT "no". A consumer that reads this as a denial turns an
    // admin-api blip into a lockout screen for the administrator.
    { authenticated: true, degraded: true, is_admin: null, admin_account: null,
      user: { id: null, email: email ?? null, name: name ?? null } },
    { headers: NO_STORE },
  );
}
