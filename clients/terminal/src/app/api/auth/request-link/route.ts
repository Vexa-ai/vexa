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
 *  failures are logged server-side instead. The one exception is a MISCONFIGURED instance (no
 *  NEXTAUTH_SECRET, so no token can be signed at all): that is a 503, because pretending to have
 *  sent a link nobody can ever receive would hide a broken deploy behind a security property it does
 *  not have — and it is the same 503 for every address.
 *
 *  This route never creates a user and never mints a session — everything happens at `redeem/`,
 *  after the recipient proves they hold the mailbox.
 */
import { NextResponse, type NextRequest } from "next/server";
import { mintMagicToken, safeNext, ttlSeconds } from "../magicToken";
import { startLinkDelivery } from "../linkDelivery";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Where the link points. Explicit env wins (the container knows its public URL; the request's own
 *  Host is whatever a proxy passed through), then the forwarded/Host headers as a last resort. */
function baseUrl(request: NextRequest): string {
  const configured = process.env.NEXTAUTH_URL || process.env.TERMINAL_URL || "";
  if (configured) return configured.replace(/\/$/, "");
  const h = request.headers;
  const proto = h.get("x-forwarded-proto") || "http";
  const host = h.get("x-forwarded-host") || h.get("host") || "localhost:3000";
  return `${proto}://${host}`;
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
  if (!EMAIL_RE.test(normalized)) {
    return NextResponse.json({ error: "Invalid email format" }, { status: 400, headers: NO_STORE });
  }

  // Minted for every well-formed address, admitted or not: whether this can succeed at all is a fact
  // about the instance, not about the address, and answering it the same way for everybody is what
  // keeps the 503 below from becoming an oracle.
  const minted = mintMagicToken(normalized);
  if (!minted.ok) {
    console.error(`[terminal-auth] magic link refused: ${minted.error}`);
    return NextResponse.json({ error: "Email sign-in is not configured on this instance." }, { status: 503, headers: NO_STORE });
  }

  const target = safeNext(typeof next === "string" ? next : null);
  const url = `${baseUrl(request)}/api/auth/redeem?t=${encodeURIComponent(minted.token)}&next=${encodeURIComponent(target)}`;
  const minutes = Math.round(ttlSeconds() / 60);

  // Admission and the send run AFTER this response (../linkDelivery.ts) — see the header.
  startLinkDelivery(normalized, url, minutes);

  return NextResponse.json({ ok: true }, { headers: NO_STORE });
}
