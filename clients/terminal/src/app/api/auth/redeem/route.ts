/** Redeem an emailed sign-in link — the BACK half of the magic-link door.
 *
 *  ⚠ PREFETCH-SAFE (live 2026-10-15, OeNB): Microsoft 365 fetches every link in Teams and Outlook
 *  before a person clicks it — `SkypeUriPreview` for the card, Defender Safe Links for the scan —
 *  and both reached this route first. When GET burned the token, the person's own click got 410 and
 *  could not sign in. So the link itself is now only a QUESTION:
 *
 *  GET  /api/auth/redeem?t=<token>&next=<relative-path>
 *    verifies signature + expiry WITHOUT consuming anything and renders a small page: "Sign in to
 *    Vexa as m…@oenb.at" (masked) with one button. No script submits it; a scanner that reads the
 *    page changes nothing. A link already expired, forged, or closed by the operator is refused here
 *    exactly as before.
 *  HEAD — 200, empty, no side effect, whatever the token.
 *  POST (the button; form fields `t`, `next`) — the only verb that signs anybody in:
 *    0. SAME-ORIGIN ONLY: `Sec-Fetch-Site` (when sent) must be `same-origin` and `Origin` (when sent)
 *       must be this instance's origin; a POST carrying neither is refused too. Otherwise another
 *       site could post its own token from a victim's browser and sign them in as somebody else
 *       (login CSRF).
 *    1. verify the HMAC signature and the expiry, and BURN the jti (single use — see magicToken.ts;
 *       burning happens BEFORE the session is minted, so a replay can never win a race with a slow
 *       admin-api round-trip. The cost is that a transient admin-api failure eats the link and the
 *       visitor asks for another one; that is the right side of the trade),
 *    2. reuse the SAME machinery the direct-login route uses — findOrCreateUserToken (which first
 *       ASKS whether this address may sign in at all, Vexa-ai/vexa#1783, and refuses before any
 *       account exists if not) + the
 *       httpOnly `vexa-token` / `vexa-user-info` cookies — so every downstream consumer
 *       (server.mjs's WS proxy, api/proxyAuth.ts, /api/auth/me, the minutes dev seams that read
 *       `id` out of the info cookie) sees a session identical to any other,
 *    3. 303 to `next`, which `safeNext` has already reduced to a site-relative path — or, when the
 *       link named no destination of its own, to a freshly minted FIRST-VISIT scaffold (F42).
 *
 *  A refused link answers with a small HTML page, not a JSON 401 — the visitor arrived by clicking
 *  a mail, and a raw error body is a dead end for them.
 */
import { NextResponse, type NextRequest } from "next/server";
import { AUTH_COOKIE, USER_INFO_COOKIE, findOrCreateUserToken, mintFirstVisitScaffold } from "../adminApi";
import { redeemMagicToken, safeNext, verifyMagicToken, type VerifyFailure } from "../magicToken";
import { SIGNIN_NOT_ALLOWED, SIGNIN_UNAVAILABLE } from "../../../signinRefusal";
import { signinMethodEnabled } from "../oidcConfig.mjs";
import { captureSigninPhoto } from "../signinPhoto";
import { isSecureRequest, sameOriginPost } from "../sameOrigin";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** One self-contained card — the refusals and the sign-in question share it. */
function card(title: string, inner: string, status: number): NextResponse {
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">
<style>
 :root{color-scheme:dark light}
 body{margin:0;min-height:100vh;min-height:100dvh;overflow-x:hidden;display:flex;align-items:center;justify-content:center;
      background:#0d0f12;color:#e6e8eb;font:14px/1.55 ui-sans-serif,system-ui,-apple-system,sans-serif}
 .card{width:340px;max-width:calc(100vw - 32px);box-sizing:border-box;background:#15181d;border:1px solid #262b33;border-radius:12px;padding:24px;
       box-shadow:0 8px 32px rgba(0,0,0,.3)}
 h1{margin:0 0 10px;font-size:15px;font-weight:600}
 p{margin:0 0 16px;font-size:12.5px;color:#9aa3af}
 a,button{display:inline-block;background:#3b82f6;color:#fff;text-decoration:none;border-radius:7px;
   padding:9px 14px;font-size:13px;font-weight:600;border:0;cursor:pointer;font-family:inherit}
</style></head><body><div class="card">
<h1>${esc(title)}</h1>${inner}
</div></body></html>`;
  return new NextResponse(html, { status, headers: { "Content-Type": "text/html; charset=utf-8", ...NO_STORE } });
}

/** The refused-link card. Almost every refusal here means the link is gone (used, expired, forged,
 *  or burned by a redeem that then could not finish), so its one link out asks for a new one. The
 *  exception is an address that may not sign in at all: a new link would not help, and a button
 *  saying it would is the card contradicting itself. */
function page(title: string, detail: string, status: number, cta = "Ask for a new link"): NextResponse {
  return card(title, `<p>${esc(detail)}</p><a href="/">${esc(cta)}</a>`, status);
}

/** `maria@oenb.at` → `m…@oenb.at`. Enough for the person to recognise their own address; not
 *  enough for whoever previewed the link to learn it. */
function maskEmail(email: string): string {
  const at = email.lastIndexOf("@");
  if (at <= 0) return "…";
  return `${email[0]}…${email.slice(at)}`;
}

/** The answer for a link that cannot be used — shared by the question (GET) and the act (POST). */
function refusal(reason: VerifyFailure): NextResponse {
  switch (reason) {
    case "used":
      return page("This link was already used", "Sign-in links work exactly once. Ask for a fresh one.", 410);
    case "expired":
      return page("This link has expired", "Sign-in links are good for a few minutes. Ask for a fresh one.", 410);
    case "unconfigured":
      return page("Email sign-in is not configured", "This instance cannot verify sign-in links.", 503);
    case "unavailable":
      return page("Sign-in is unavailable", "We could not check this link just now. It has not been used; try it again in a minute.", 503);
    default:
      return page("This sign-in link is not valid", "The link looks incomplete or altered. Ask for a fresh one.", 400);
  }
}

function closedDoor(): NextResponse {
  return page("Email sign-in is not offered here", "Sign in with one of the options on the sign-in page.", 404, "Back to sign-in");
}

/** WHERE A SIGN-IN LANDS (F42, founder ruling 2026-09-02; amended Vexa-ai/vexa#1591).
 *
 *  A link that named a destination keeps it — an invite, a meeting page, a scaffold someone already
 *  minted for this person (`?s=`). Everything else used to land on `/`, and `/` was the product
 *  composing a landing out of whatever was lying around: a seeded "Personal" chat on the generic
 *  greeting, an admin-only setup card offered to a plain member, an empty desk's README template
 *  rendered as a page of `(unset)`. *"i logged as new user, that's what i see - not happy about
 *  that."*
 *
 *  So a sign-in with nowhere to go MINTS ITS OWN ARRIVAL. The record — which workspaces are already
 *  shared with this address, which meetings it is invited to — is derived server-side; nothing about
 *  it is guessed here.
 *
 *  ⚠ AND ONLY WHEN THERE IS NOWHERE TO GO. "The link named no destination" is a fact about the
 *  link; "this person has nothing to return to" is a fact about the person, and F42 used the first
 *  as though it were the second. So the admin who had spent a morning here signed in again and was
 *  introduced to the product — *"i logged in again and now see no chats and it's starting over
 *  again while it has the context"*. The question is now asked of the server, which holds both
 *  answers (their chat threads, their desk); `mintFirstVisitScaffold` owns it, so all four sign-in
 *  doors ask it the same way. A returning person lands on `/`, where the rail — since #1591, derived
 *  from those same sessions — is their own chats.
 *
 *  NO ARRIVAL COSTS NOTHING BUT THE ARRIVAL: the visitor lands on `/` exactly as before, signed in.
 *  The opposite trade from the admin claim, where the role had already changed and a silent failure
 *  would strand the new administrator — here the only thing that has happened is that they are
 *  signed in, which is what they came for. */
async function arrival(target: string, email: string, userId: string | number): Promise<string> {
  // `?s=` inside the destination means an arrival already exists for this click — minting a second
  // would open a conversation over the one they were sent.
  if (target !== "/" || /[?&]s=/.test(target)) return target;
  const minted = await mintFirstVisitScaffold(email, userId);
  if (minted.ok && minted.data?.url) return minted.data.url;
  // 409 is the DELIBERATE no-arrival — a returning person, or a probe that could not answer. It is
  // the ordinary path for everybody who has been here before, so it is not an error and must not be
  // logged as one: a line that cries wolf on the common case is a line nobody reads on the rare one.
  if (minted.status === 409) console.info(`[terminal-auth] ${minted.error}`);
  else console.error(`[terminal-auth] first-visit scaffold mint failed for ${email}: ${minted.ok ? "no url" : minted.error}`);
  return target;
}

/** The link itself: a QUESTION, never the act. Anything that fetches it — a person, a Teams preview,
 *  a mail scanner — sees the same page and changes nothing. */
export async function GET(request: NextRequest) {
  // CLOSED BY THE OPERATOR (`VEXA_SIGNIN_METHODS` without `email`): a link minted before the door was
  // closed signs nobody in after it.
  if (!signinMethodEnabled("email")) return closedDoor();
  const token = request.nextUrl.searchParams.get("t") || "";
  const target = safeNext(request.nextUrl.searchParams.get("next"));

  // Signature + expiry only: whether the link was already used is learnt only by spending it, and
  // spending it is the POST's job.
  const verdict = verifyMagicToken(token);
  if (!verdict.ok) return refusal(verdict.reason);

  const who = maskEmail(verdict.email);
  return card(
    `Sign in to Vexa`,
    `<p>Sign in to Vexa as <strong>${esc(who)}</strong>.</p>
<form method="post" action="/api/auth/redeem">
<input type="hidden" name="t" value="${esc(token)}">
<input type="hidden" name="next" value="${esc(target)}">
<button type="submit">Sign in</button>
</form>`,
    200,
  );
}

/** Link previews and scanners often only ask whether the link is there. It is; nothing happens. */
export async function HEAD() {
  return new NextResponse(null, { status: 200, headers: NO_STORE });
}

/** The button: the only request that spends the link and signs somebody in. */
export async function POST(request: NextRequest) {
  if (!signinMethodEnabled("email")) return closedDoor();
  if (!sameOriginPost(request)) {
    console.info("[terminal-auth] magic-link redeem refused: cross-site POST");
    return page("Sign in from the link itself", "Open the sign-in link from your email and press Sign in there.", 403);
  }
  // Read once: a form body can only be consumed one time.
  let token = "";
  let rawNext: string | null = null;
  try {
    const form = await request.formData();
    const t = form.get("t");
    const n = form.get("next");
    token = typeof t === "string" ? t : "";
    rawNext = typeof n === "string" ? n : null;
  } catch { /* not a form body — fall back to the query */ }
  if (!token) token = request.nextUrl.searchParams.get("t") || "";
  if (rawNext === null) rawNext = request.nextUrl.searchParams.get("next");
  const target = safeNext(rawNext);

  const verdict = await redeemMagicToken(token);
  if (!verdict.ok) return refusal(verdict.reason);

  // CHECKED AGAIN HERE, not only when the link was sent (Vexa-ai/vexa#1783): the address may have
  // been taken off the allow-list in the minutes between, and a link is a bearer credential that
  // outlives the decision that sent it. `findOrCreateUserToken` asks before it creates anything.
  //
  // AFTER the burn, deliberately. Admission depends only on the address inside the token, never on
  // who clicked, so checking first would protect nothing — and it would cost an admin-api round-trip
  // on every replayed, expired or forged link, which today are answered without one. The price is
  // that a refusal spends the link; for an address that may not sign in that is no loss, and for an
  // admin-api outage it is the trade the burn already makes (ask for a fresh link).
  // the link was redeemed: the inbox is proven, and identity records the address as verified
  const result = await findOrCreateUserToken(verdict.email, { emailVerifiedVia: "magic_link" });
  if (!result.ok) {
    if (result.refused === "not-allowed") {
      console.info(`[terminal-auth] magic-link redeem refused: ${verdict.email} is not allowed to sign in`);
      return page("You can’t sign in here", SIGNIN_NOT_ALLOWED, 403, "Back to sign-in");
    }
    if (result.refused === "unavailable") {
      console.error(`[terminal-auth] magic-link redeem refused, admission unavailable (fail closed): ${result.error}`);
      return page("Sign-in is unavailable", `${SIGNIN_UNAVAILABLE} This link has been used; ask for a fresh one.`, 503);
    }
    console.error(`[terminal-auth] magic-link redeem failed after verification: ${result.error}`);
    return page("Could not complete sign-in", "Something on our side failed. Ask for a fresh link and try again.", 502);
  }

  const { user, token: apiToken } = result;
  // An emailed link carries no picture; identity may check the person's own Gravatar (UI 15g). Not
  // awaited — it never holds up the sign-in.
  void captureSigninPhoto(user.id, {});
  // 303: the browser follows a POST's redirect with a GET, which is what landing on a page is.
  const res = new NextResponse(null, {
    status: 303,
    headers: { Location: await arrival(target, user.email, user.id), ...NO_STORE },
  });
  const opts = { httpOnly: true, secure: isSecureRequest(), sameSite: "lax" as const, maxAge: 60 * 60 * 24 * 30, path: "/" };
  res.cookies.set(AUTH_COOKIE, apiToken, opts);
  res.cookies.set(
    USER_INFO_COOKIE,
    JSON.stringify({ id: user.id, email: user.email, name: user.name || user.email.split("@")[0] }),
    opts,
  );
  return res;
}
