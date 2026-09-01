/** Redeem an emailed sign-in link — the BACK half of the magic-link door.
 *
 *  GET /api/auth/redeem?t=<token>&next=<relative-path>
 *    1. verify the HMAC signature and the expiry, and BURN the jti (single use — see magicToken.ts;
 *       burning happens BEFORE the session is minted, so a replay can never win a race with a slow
 *       admin-api round-trip. The cost is that a transient admin-api failure eats the link and the
 *       visitor asks for another one; that is the right side of the trade),
 *    2. reuse the SAME machinery the direct-login route uses — findOrCreateUserToken + the
 *       httpOnly `vexa-token` / `vexa-user-info` cookies — so every downstream consumer
 *       (server.mjs's WS proxy, api/proxyAuth.ts, /api/auth/me, the minutes dev seams that read
 *       `id` out of the info cookie) sees a session identical to any other,
 *    3. 302 to `next`, which `safeNext` has already reduced to a site-relative path.
 *
 *  A refused link answers with a small HTML page, not a JSON 401 — the visitor arrived by clicking
 *  a mail, and a raw error body is a dead end for them.
 */
import { NextResponse, type NextRequest } from "next/server";
import { AUTH_COOKIE, USER_INFO_COOKIE, findOrCreateUserToken } from "../adminApi";
import { redeemMagicToken, safeNext } from "../magicToken";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

/** Mirrors login/ and the OAuth signIn callback: cookies are Secure iff the deploy is HTTPS. */
function isSecureRequest(): boolean {
  return (
    (process.env.TERMINAL_URL || "").startsWith("https://") ||
    (process.env.NEXTAUTH_URL || "").startsWith("https://")
  );
}

function page(title: string, detail: string, status: number): NextResponse {
  const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{color-scheme:dark light}
 body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
      background:#0d0f12;color:#e6e8eb;font:14px/1.55 ui-sans-serif,system-ui,-apple-system,sans-serif}
 .card{width:340px;background:#15181d;border:1px solid #262b33;border-radius:12px;padding:24px;
       box-shadow:0 8px 32px rgba(0,0,0,.3)}
 h1{margin:0 0 10px;font-size:15px;font-weight:600}
 p{margin:0 0 16px;font-size:12.5px;color:#9aa3af}
 a{display:inline-block;background:#3b82f6;color:#fff;text-decoration:none;border-radius:7px;
   padding:9px 14px;font-size:13px;font-weight:600}
</style></head><body><div class="card">
<h1>${esc(title)}</h1><p>${esc(detail)}</p><a href="/">Ask for a new link</a>
</div></body></html>`;
  return new NextResponse(html, { status, headers: { "Content-Type": "text/html; charset=utf-8", ...NO_STORE } });
}

export async function GET(request: NextRequest) {
  const token = request.nextUrl.searchParams.get("t") || "";
  const target = safeNext(request.nextUrl.searchParams.get("next"));

  const verdict = redeemMagicToken(token);
  if (!verdict.ok) {
    switch (verdict.reason) {
      case "used":
        return page("This link was already used", "Sign-in links work exactly once. Ask for a fresh one.", 410);
      case "expired":
        return page("This link has expired", "Sign-in links are good for a few minutes. Ask for a fresh one.", 410);
      case "unconfigured":
        return page("Email sign-in is not configured", "This instance cannot verify sign-in links.", 503);
      default:
        return page("This sign-in link is not valid", "The link looks incomplete or altered. Ask for a fresh one.", 400);
    }
  }

  const result = await findOrCreateUserToken(verdict.email);
  if (!result.ok) {
    console.error(`[terminal-auth] magic-link redeem failed after verification: ${result.error}`);
    return page("Could not complete sign-in", "Something on our side failed. Ask for a fresh link and try again.", 502);
  }

  const { user, token: apiToken } = result;
  const res = new NextResponse(null, { status: 302, headers: { Location: target, ...NO_STORE } });
  const opts = { httpOnly: true, secure: isSecureRequest(), sameSite: "lax" as const, maxAge: 60 * 60 * 24 * 30, path: "/" };
  res.cookies.set(AUTH_COOKIE, apiToken, opts);
  res.cookies.set(
    USER_INFO_COOKIE,
    JSON.stringify({ id: user.id, email: user.email, name: user.name || user.email.split("@")[0] }),
    opts,
  );
  return res;
}
