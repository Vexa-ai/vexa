/** Logout — clears EVERY auth cookie so no stale session survives. Beyond the terminal's own
 *  `vexa-token` / `vexa-user-info`, NextAuth writes a family of cookies (session, csrf, callback-url,
 *  state, pkce, nonce) and prefixes them `__Secure-` / `__Host-` when cookies are secure (HTTPS). We
 *  don't know at request time which prefix is live, so we expire BOTH the plain and prefixed forms. The
 *  client (UserProfile sign-out) additionally wipes local/sessionStorage and reloads.
 *
 *  We also expire each non-`__Host-` cookie on the HOST and on every PARENT domain (host-only + `.parent`).
 *  A token scoped to a shared parent (e.g. `.vexa.ai`, where the platform webapp reuses the same OAuth
 *  app) is sent to this host but a host-only deletion can't remove it — so logout would "do nothing" and
 *  trap the user at a stale-token 401 until they hand-cleared cookies. Clearing every scope ends that.
 *
 *  Note: for Google/Microsoft this drops the app session only — those providers keep their own, and
 *  `prompt=select_account` (see authOptions) is what lets the user then choose a different account.
 *  That is NOT sufficient for an OIDC/SSO deploy: there the gate hands straight back to the provider
 *  with no card in between, so a live provider session silently re-authenticates and sign-out looks
 *  like it did nothing. So we also return `endSessionUrl` — OIDC RP-Initiated Logout — and the client
 *  navigates there to end the provider session too. Cookies are still cleared here either way, so a
 *  client that ignores the URL is no worse off than before. */
import { NextResponse, type NextRequest } from "next/server";
import { headers } from "next/headers";
import { getToken } from "next-auth/jwt";
import { AUTH_COOKIE, USER_INFO_COOKIE } from "../adminApi";

export const dynamic = "force-dynamic";

/** Mirror how login / the OAuth signIn callback choose `secure` (TERMINAL_URL / NEXTAUTH_URL https).
 *  A cookie set with Secure can only be cleared by a Secure Set-Cookie — Chrome's "leave secure cookies
 *  alone" rule makes the browser refuse a non-Secure overwrite, so a stale `vexa-token` would survive
 *  logout on an HTTPS deploy. We must expire with the SAME `secure` the cookie was written with. */
function isSecureRequest(): boolean {
  return (
    (process.env.TERMINAL_URL || "").startsWith("https://") ||
    (process.env.NEXTAUTH_URL || "").startsWith("https://")
  );
}

// Base names NextAuth uses; each is cleared in plain, __Secure-, and (csrf/host) __Host- form below.
const NEXTAUTH_BASES = [
  "next-auth.session-token",
  "next-auth.csrf-token",
  "next-auth.callback-url",
  "next-auth.state",
  "next-auth.pkce.code_verifier",
  "next-auth.nonce",
];

/** Domain scopes to clear a cookie on: host-only (no Domain attr) + each parent down to a 2-label
 *  registrable-ish suffix. For `browser.dev.vexa.ai` → [host-only, .dev.vexa.ai, .vexa.ai]. We stop at
 *  2 labels so we never emit a public-suffix domain (`.ai`), which the browser would reject anyway. */
function domainScopes(host: string): (string | undefined)[] {
  const bare = (host || "").split(":")[0].replace(/^\.+/, "");
  const scopes: (string | undefined)[] = [undefined]; // host-only
  // IPs / single-label hosts (localhost) have no usable parent domain.
  if (/^\d+\.\d+\.\d+\.\d+$/.test(bare) || bare.split(".").length < 2) return scopes;
  const labels = bare.split(".");
  for (let i = 0; labels.length - i >= 2; i++) {
    scopes.push("." + labels.slice(i).join("."));
  }
  return scopes;
}

/** Serialize one expiring Set-Cookie. `__Host-` cookies are host-only by spec (no Domain allowed), so we
 *  only emit those host-only; everything else is emitted once per domain scope. */
function expireCookie(name: string, secure: boolean, domain?: string): string {
  if (domain && name.startsWith("__Host-")) return "";
  const parts = [`${name}=`, "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax"];
  if (secure) parts.push("Secure");
  if (domain) parts.push(`Domain=${domain}`);
  return parts.join("; ");
}

/** OIDC RP-Initiated Logout URL for the configured provider, or undefined when SSO is not in use.
 *  Read the id_token BEFORE the cookies below are expired — it lives in the NextAuth session we are
 *  about to destroy. `post_logout_redirect_uri` must be registered on the client (the realm ships
 *  `post.logout.redirect.uris`), otherwise Keycloak refuses the redirect. */
async function endSessionUrl(req: NextRequest): Promise<string | undefined> {
  const issuer = (process.env.KEYCLOAK_ISSUER || "").replace(/\/$/, "");
  if (!issuer) return undefined;
  const token = await getToken({ req, secret: process.env.NEXTAUTH_SECRET }).catch(() => null);
  const idToken = (token as { idToken?: string } | null)?.idToken;

  const origin = process.env.NEXTAUTH_URL || req.nextUrl.origin;
  const url = new URL(`${issuer}/protocol/openid-connect/logout`);
  url.searchParams.set("post_logout_redirect_uri", origin);
  if (idToken) {
    // The clean path: the provider ends the session outright, no interstitial.
    url.searchParams.set("id_token_hint", idToken);
  } else {
    // No id_token — a session minted before this route stored one, or a provider that withheld it.
    // `client_id` is the spec's alternative and still ends the session; the provider may interpose a
    // confirmation page. Worth it: without SOME end-session URL the user cannot break out of the
    // silent re-authentication loop at all, which is the bug this exists to fix.
    url.searchParams.set("client_id", process.env.KEYCLOAK_CLIENT_ID || "");
  }
  return url.toString();
}

export async function POST(req: NextRequest) {
  const host = (await headers()).get("host") || "";
  const httpsDeploy = isSecureRequest();
  const ssoLogout = await endSessionUrl(req);
  const res = NextResponse.json(
    { success: true, endSessionUrl: ssoLogout },
    { headers: { "Cache-Control": "no-store" } },
  );

  const emit = (name: string, scopes: (string | undefined)[]) => {
    // `__`-prefixed names MUST carry Secure (prefix rule); plain names were set Secure on an HTTPS deploy
    // (login / signIn / NextAuth useSecureCookies all key off the same https check) and must be cleared
    // Secure to match, else Chrome refuses the overwrite. On a non-HTTPS (localhost) deploy they're plain.
    const secure = name.startsWith("__") || httpsDeploy;
    for (const domain of scopes) {
      const cookie = expireCookie(name, secure, domain);
      if (cookie) res.headers.append("Set-Cookie", cookie);
    }
  };

  // The terminal's OWN cookies are the ones that may have been written on a shared PARENT domain
  // (`.vexa.ai`, where the platform webapp reuses the same OAuth app) — a host-only deletion can't reach
  // those, which is what trapped users at a stale-token 401. Clear them across host + every parent scope.
  for (const name of [AUTH_COOKIE, USER_INFO_COOKIE]) emit(name, domainScopes(host));

  // NextAuth always writes its cookies host-scoped (no Domain), so host-only clearing suffices. Keeping
  // these host-only also keeps the total Set-Cookie header size small — emitting every NextAuth name ×
  // prefix × domain scope overflowed nginx's proxy_buffer_size and 502'd the whole logout.
  for (const base of NEXTAUTH_BASES) {
    emit(base, [undefined]);
    emit(`__Secure-${base}`, [undefined]);
    emit(`__Host-${base}`, [undefined]);
  }

  return res;
}
