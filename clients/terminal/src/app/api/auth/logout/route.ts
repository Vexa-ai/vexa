/** Logout — clears EVERY auth cookie so no stale session survives. Beyond the terminal's own
 *  `vexa-token` / `vexa-user-info`, NextAuth writes a family of cookies (session, csrf, callback-url,
 *  state, pkce, nonce) and prefixes them `__Secure-` / `__Host-` when cookies are secure (HTTPS). We
 *  don't know at request time which prefix is live, so we expire BOTH the plain and prefixed forms. The
 *  client (UserProfile sign-out) additionally wipes local/sessionStorage and reloads.
 *
 *  Note: this drops the app session only. The OAuth *provider* (Google/Microsoft) keeps its own session
 *  — `prompt=select_account` (see authOptions) is what lets the user then choose a different account. */
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { AUTH_COOKIE, USER_INFO_COOKIE } from "../adminApi";

export const dynamic = "force-dynamic";

// Base names NextAuth uses; each is cleared in plain, __Secure-, and (csrf/host) __Host- form below.
const NEXTAUTH_BASES = [
  "next-auth.session-token",
  "next-auth.csrf-token",
  "next-auth.callback-url",
  "next-auth.state",
  "next-auth.pkce.code_verifier",
  "next-auth.nonce",
];

export async function POST() {
  const cookieStore = await cookies();

  const names = new Set<string>([AUTH_COOKIE, USER_INFO_COOKIE]);
  for (const base of NEXTAUTH_BASES) {
    names.add(base);
    names.add(`__Secure-${base}`);
    names.add(`__Host-${base}`);
  }

  // Expire each cookie. `__Secure-`/`__Host-`-prefixed names MUST carry Secure or the browser rejects the
  // Set-Cookie (prefix rule) and the cookie survives; plain names use Secure=false so they also clear on a
  // non-HTTPS (localhost) deploy. Deletion matches on name+path, so this is sufficient.
  for (const name of names) {
    const secure = name.startsWith("__");
    cookieStore.set(name, "", { maxAge: 0, path: "/", secure, sameSite: "lax", httpOnly: true });
  }

  return NextResponse.json({ success: true }, { headers: { "Cache-Control": "no-store" } });
}
