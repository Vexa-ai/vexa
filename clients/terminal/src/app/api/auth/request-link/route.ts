/** Ask for an emailed sign-in link — the FRONT half of the magic-link door.
 *
 *  POST {email, next?} → mints a short-lived HMAC-signed token and mails
 *      <base>/api/auth/redeem?t=<token>&next=<relative-path>
 *  `next` carries the deeplink the visitor was already reaching for (`?ask=`, `?meeting=`,
 *  `?view=`), so one click is door AND destination: click → authenticated → primed chat, one hop.
 *
 *  NO USER ENUMERATION: a well-formed address always gets the same 200, whether or not it is
 *  known here, whether or not the mail actually went out. Nothing about the account (or about the
 *  mail transport's health) may be inferred from this response — delivery failures are logged
 *  server-side instead. The one exception is a MISCONFIGURED instance (no NEXTAUTH_SECRET, so no
 *  token can be signed at all): that is a 503, because pretending to have sent a link nobody can
 *  ever receive would hide a broken deploy behind a security property it does not have.
 *
 *  This route never creates a user and never mints a session — everything happens at `redeem/`,
 *  after the recipient proves they hold the mailbox.
 */
import { NextResponse, type NextRequest } from "next/server";
import { mintMagicToken, safeNext, ttlSeconds } from "../magicToken";
import { sendMail } from "../mailer";

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

  const minted = mintMagicToken(normalized);
  if (!minted.ok) {
    console.error(`[terminal-auth] magic link refused: ${minted.error}`);
    return NextResponse.json({ error: "Email sign-in is not configured on this instance." }, { status: 503, headers: NO_STORE });
  }

  const target = safeNext(typeof next === "string" ? next : null);
  const url = `${baseUrl(request)}/api/auth/redeem?t=${encodeURIComponent(minted.token)}&next=${encodeURIComponent(target)}`;
  const minutes = Math.round(ttlSeconds() / 60);

  try {
    await sendMail({
      to: normalized,
      subject: "Your Vexa sign-in link",
      text: `Sign in to Vexa:\n\n${url}\n\nThe link works once and expires in ${minutes} minutes.\n`,
    });
  } catch (err) {
    // Never surfaced: the caller must not learn whether the address exists OR whether mail works.
    console.error(`[terminal-auth] sign-in link delivery failed: ${(err as Error).message}`);
  }

  return NextResponse.json({ ok: true }, { headers: NO_STORE });
}
