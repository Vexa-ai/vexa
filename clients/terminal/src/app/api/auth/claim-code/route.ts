/** Enter the admin claim code — the first step of claiming an instance nobody has claimed yet.
 *
 *  POST {code} → admin-api says whether it is the live one-time code (written to admin-api's log at
 *  boot). A live code is kept in an httpOnly cookie scoped to /api/auth, so the sign-in that follows —
 *  the emailed link (opened in this browser), Google or Microsoft — carries it to admin-api, which
 *  admits that sign-in and makes it the administrator. A wrong code is said so at once, here, rather
 *  than as a link that never arrives: the email form answers the same for every address by design.
 *
 *  The cookie holds nothing a guess could not: the code is checked by admin-api on every use, is
 *  retired by the claim that uses it, and an instance with an administrator answers no to all of it.
 */
import { NextResponse, type NextRequest } from "next/server";
import { CLAIM_COOKIE, checkClaimCode } from "../adminApi";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;
const MAX_AGE_S = 3600;

function isSecureRequest(): boolean {
  return (
    (process.env.TERMINAL_URL || "").startsWith("https://") ||
    (process.env.NEXTAUTH_URL || "").startsWith("https://")
  );
}

export async function POST(request: NextRequest) {
  let code: unknown;
  try {
    ({ code } = await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400, headers: NO_STORE });
  }
  if (typeof code !== "string" || !code.trim() || code.length > 64) {
    return NextResponse.json({ error: "Enter the claim code." }, { status: 400, headers: NO_STORE });
  }
  const valid = await checkClaimCode(code.trim());
  if (valid === null) {
    return NextResponse.json({ error: "Could not check the code — try again in a moment." }, { status: 503, headers: NO_STORE });
  }
  if (!valid) {
    return NextResponse.json(
      { error: "That is not this instance's claim code. Find the current one in the admin-api log." },
      { status: 403, headers: NO_STORE },
    );
  }
  const res = NextResponse.json({ ok: true }, { headers: NO_STORE });
  res.cookies.set(CLAIM_COOKIE, code.trim(), {
    httpOnly: true, sameSite: "lax", secure: isSecureRequest(), path: "/api/auth", maxAge: MAX_AGE_S,
  });
  return res;
}
