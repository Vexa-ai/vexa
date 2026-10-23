/** The Company profile switch — an instance administrator works as the company without a second
 *  sign-in (ADR-0049 § The Company profile).
 *
 *  The company layer has one writer: the deployment's admin account (identity.v1 `admin_account`,
 *  #1835). This route lets an everyday account that is this instance's administrator (`is_admin`,
 *  from the oracle) put that account's session on its own tab and take it off again:
 *
 *  - `POST {to: "company"}` — the caller's token must validate as `is_admin` and not as the admin
 *    account. The admin account is found by the address `VEXA_ADMIN_ACCOUNT` names (the same value
 *    admin-api reads); a token is minted for it under the name `company:<user id>` (so the token list
 *    says whose Company session it is) with a 12-hour life, and it counts only when the oracle then
 *    answers `admin_account: true` for it — positive evidence, never the address alone. The person's
 *    own token moves to `vexa-person-token`; the auth cookie carries the company session.
 *  - `POST {to: "me"}` — the person's own token is validated and restored, and the company token is
 *    revoked. Nothing about the person's own session changed while they were away.
 *
 *  Nothing here is an authorization boundary of its own: every request made in Company mode carries
 *  the admin account's token, and the services confine that account exactly as they confine it when
 *  somebody signs in as it. Every switch is logged with the person's id and never with a token. */
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import {
  AUTH_COOKIE, USER_INFO_COOKIE, createUser, findUserByEmail, mintUserToken, revokeToken, validateAuthToken,
} from "../adminApi";
import {
  COMPANY_TOKEN_ID_COOKIE, COMPANY_TOKEN_SECONDS, PERSON_COOKIE, PERSON_INFO_COOKIE, adminAccountAddresses,
} from "../companyMode";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

function isSecureRequest(): boolean {
  return (
    (process.env.TERMINAL_URL || "").startsWith("https://") ||
    (process.env.NEXTAUTH_URL || "").startsWith("https://")
  );
}

function refuse(status: number, detail: string): NextResponse {
  return NextResponse.json({ ok: false, detail }, { status, headers: NO_STORE });
}

function log(event: string, fields: Record<string, unknown>): void {
  console.log(JSON.stringify({ event, service: "terminal", ...fields }));
}

export async function POST(req: Request) {
  let to: unknown;
  try { ({ to } = (await req.json()) as { to?: unknown }); } catch { return refuse(422, "Body must be {to: \"company\" | \"me\"}"); }
  if (to !== "company" && to !== "me") return refuse(422, "Body must be {to: \"company\" | \"me\"}");

  const jar = await cookies();
  const opts = { httpOnly: true, secure: isSecureRequest(), sameSite: "lax" as const, path: "/" };

  if (to === "me") {
    const own = jar.get(PERSON_COOKIE)?.value;
    if (!own) return refuse(409, "This tab is not working as the company");
    const person = await validateAuthToken(own);
    if (!person.ok) return refuse(person.status === 401 ? 401 : 503, "Your own session could not be restored — sign in again");
    const tid = jar.get(COMPANY_TOKEN_ID_COOKIE)?.value;
    if (tid) await revokeToken(tid).catch(() => undefined);
    const res = NextResponse.json({ ok: true, mode: "me" }, { headers: NO_STORE });
    res.cookies.set(AUTH_COOKIE, own, { ...opts, maxAge: 60 * 60 * 24 * 30 });
    const info = jar.get(PERSON_INFO_COOKIE)?.value;
    if (info) res.cookies.set(USER_INFO_COOKIE, info, { ...opts, maxAge: 60 * 60 * 24 * 30 });
    for (const c of [PERSON_COOKIE, PERSON_INFO_COOKIE, COMPANY_TOKEN_ID_COOKIE]) res.cookies.set(c, "", { ...opts, maxAge: 0 });
    log("company_switch", { to: "me", user_id: person.userId });
    return res;
  }

  // to === "company"
  if (jar.get(PERSON_COOKIE)?.value) return refuse(409, "This tab is already working as the company");
  const token = jar.get(AUTH_COOKIE)?.value;
  if (!token) return refuse(401, "Not signed in");
  const me = await validateAuthToken(token);
  if (!me.ok) return refuse(me.status === 401 ? 401 : 503, me.status === 401 ? "Not signed in" : "Sign-in could not be checked");
  if (me.adminAccount) return refuse(409, "This is the admin account already");
  if (!me.isAdmin) return refuse(403, "Only this instance's administrators can work as the company");

  const addresses = adminAccountAddresses();
  if (!addresses.length) return refuse(409, "This deployment names no admin account (VEXA_ADMIN_ACCOUNT)");

  for (const address of addresses) {
    // The admin account comes into being the first time an administrator opens it: admin-api stamps
    // `account: admin` only on the row it creates for a named address, so creating it here is the
    // same act as its first sign-in, and an everyday account is never promoted.
    let found = await findUserByEmail(address);
    if (found.notFound) found = await createUser(address);
    if (!found.ok || !found.data) continue;
    const minted = await mintUserToken(found.data.id, {
      scopes: ["bot", "tx", "browser"], name: `company:${me.userId}`, expiresIn: COMPANY_TOKEN_SECONDS,
      credentialClass: "system",
    });
    if (!minted.ok || !minted.data?.token) continue;
    // Positive evidence: the oracle must say this token IS the admin account.
    const check = await validateAuthToken(minted.data.token);
    if (!check.ok || !check.adminAccount) {
      if (minted.data.id !== undefined) await revokeToken(minted.data.id).catch(() => undefined);
      continue;
    }
    const res = NextResponse.json({ ok: true, mode: "company" }, { headers: NO_STORE });
    res.cookies.set(PERSON_COOKIE, token, { ...opts, maxAge: 60 * 60 * 24 * 30 });
    const ownInfo = jar.get(USER_INFO_COOKIE)?.value
      ?? JSON.stringify({ id: me.userId, email: me.email, name: me.email.split("@")[0] });
    res.cookies.set(PERSON_INFO_COOKIE, ownInfo, { ...opts, maxAge: 60 * 60 * 24 * 30 });
    res.cookies.set(AUTH_COOKIE, minted.data.token, { ...opts, maxAge: COMPANY_TOKEN_SECONDS });
    res.cookies.set(USER_INFO_COOKIE, JSON.stringify({ id: found.data.id, email: check.email, name: "Company" }),
      { ...opts, maxAge: COMPANY_TOKEN_SECONDS });
    if (minted.data.id !== undefined) {
      res.cookies.set(COMPANY_TOKEN_ID_COOKIE, String(minted.data.id), { ...opts, maxAge: COMPANY_TOKEN_SECONDS });
    }
    log("company_switch", { to: "company", user_id: me.userId, admin_account_id: found.data.id });
    return res;
  }
  return refuse(503, "The admin account could not be opened — check that it exists (POST /admin/users)");
}
