/** Ask for an emailed sign-in link — the FRONT half of the magic-link door.
 *
 *  POST {email, next?} → mints a short-lived HMAC-signed token and mails
 *      <base>/api/auth/redeem?t=<token>&next=<relative-path>
 *  `next` carries the deeplink the visitor was already reaching for (`?ask=`, `?meeting=`,
 *  `?view=`), so one click is door AND destination: click → authenticated → primed chat, one hop.
 *
 *  ONLY TO AN ADDRESS THAT MAY SIGN IN (Vexa-ai/vexa#1783). Before anything is mailed, admin-api is
 *  asked whether this address is an existing user, an admin, or on the instance's allow-list
 *  (`signinAdmission`). A refused address gets no mail; so does every address while admin-api
 *  cannot answer, because the door fails closed. The redeem half asks again.
 *
 *  NO USER ENUMERATION — AND NO ALLOW-LIST ENUMERATION: a well-formed address always gets the same
 *  200 with the same body, whether it is known here, allowed here, or neither, and whether or not the
 *  mail went out. That holds for TIME as well as for content: the admission question and the send
 *  both run AFTER the response is written (`../linkDelivery.ts`), so a refused address does not answer
 *  faster than an allowed one by the length of an SMTP round-trip. Nothing about the account, the
 *  list, or the mail transport's health may be inferred from this response — refusals and delivery
 *  failures are logged server-side instead. The one exception is a MISCONFIGURED instance (no usable
 *  signing secret, or no configured public URL to build the link from): that is a 503, because pretending to have
 *  sent a link nobody can ever receive would hide a broken deploy behind a security property it does
 *  not have — and it is the same 503 for every address.
 *
 *  RATE LIMITED per client address and per email address (`../linkRateLimit.ts`): past the client
 *  limit a 429, past the address limit the usual 200 with nothing sent.
 *
 *  This route never creates a user and never mints a session — everything happens at `redeem/`,
 *  after the recipient proves they hold the mailbox.
 */
import { NextResponse, type NextRequest } from "next/server";
import { mintMagicToken, safeNext, ttlSeconds } from "../magicToken";
import { startLinkDelivery } from "../linkDelivery";
import { takeLinkRequest } from "../linkRateLimit";
import { CLIENT_ADDRESS_HEADER } from "../clientAddress.mjs";
import { isWellFormedEmail } from "../emailAddress";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

/** Where the link points: the CONFIGURED public URL (`NEXTAUTH_URL`, else `TERMINAL_URL`), and nothing
 *  else. Never the request's Host or X-Forwarded-* headers — whoever asks for a link can set those,
 *  and the link goes to somebody else's mailbox. A value that is not a plain absolute http(s) URL
 *  (no credentials) counts as unset. Null means the emailed door is not configured. */
function baseUrl(): string | null {
  const configured = (process.env.NEXTAUTH_URL || process.env.TERMINAL_URL || "").trim();
  if (!configured) return null;
  let url: URL;
  try {
    url = new URL(configured);
  } catch {
    return null;
  }
  if ((url.protocol !== "https:" && url.protocol !== "http:") || !url.hostname || url.username || url.password) return null;
  return `${url.origin}${url.pathname}`.replace(/\/+$/, "");
}

export async function POST(request: NextRequest) {
  let body: { email?: unknown; next?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400, headers: NO_STORE });
  }

  const { email, next } = body;
  if (typeof email !== "string" || !email.trim()) {
    return NextResponse.json({ error: "Email is required" }, { status: 400, headers: NO_STORE });
  }
  const normalized = email.trim().toLowerCase();
  if (!isWellFormedEmail(normalized)) {
    return NextResponse.json({ error: "Invalid email format" }, { status: 400, headers: NO_STORE });
  }

  // Minted for every well-formed address, admitted or not: whether this can succeed at all is a fact
  // about the instance, not about the address, and answering it the same way for everybody is what
  // keeps the 503 below from becoming an oracle.
  // RATE LIMITED (../linkRateLimit.ts), before anything costs an admin-api call or a mail. Over the
  // client limit: a 429 that says nothing about the address. Over the address limit: the same 200 as
  // always, and nothing is sent — the mailbox is not flooded, and the answer reveals nothing.
  const verdict = takeLinkRequest(request.headers.get(CLIENT_ADDRESS_HEADER) || "unknown", normalized);
  if (verdict === "client-limited") {
    return NextResponse.json({ error: "Too many sign-in requests. Try again in a few minutes." },
      { status: 429, headers: { ...NO_STORE, "Retry-After": "60" } });
  }
  if (verdict === "address-limited") {
    console.info("[terminal-auth] sign-in link NOT sent — too many requested for this address recently");
    return NextResponse.json({ ok: true }, { headers: NO_STORE });
  }

  const base = baseUrl();
  if (!base) {
    console.error("[terminal-auth] magic link refused: no public URL configured (NEXTAUTH_URL or TERMINAL_URL)");
    return NextResponse.json({ error: "Email sign-in is not configured on this instance." }, { status: 503, headers: NO_STORE });
  }
  const minted = mintMagicToken(normalized);
  if (!minted.ok) {
    console.error(`[terminal-auth] magic link refused: ${minted.error}`);
    return NextResponse.json({ error: "Email sign-in is not configured on this instance." }, { status: 503, headers: NO_STORE });
  }

  const target = safeNext(typeof next === "string" ? next : null);
  const url = `${base}/api/auth/redeem?t=${encodeURIComponent(minted.token)}&next=${encodeURIComponent(target)}`;
  const minutes = Math.round(ttlSeconds() / 60);

  // Admission and the send run AFTER this response (../linkDelivery.ts) — see the header.
  startLinkDelivery(normalized, url, minutes);

  return NextResponse.json({ ok: true }, { headers: NO_STORE });
}
