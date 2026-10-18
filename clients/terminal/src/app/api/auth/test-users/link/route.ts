/** `POST {email}` → a one-time sign-in link for a reserved-domain test user, minted exactly as the
 *  emailed door mints one; redeeming it is the ordinary `/api/auth/redeem` (admission included). */
import { NextResponse, type NextRequest } from "next/server";
import { mintMagicToken, ttlSeconds } from "../../magicToken";
import { NO_STORE, adminGate, isTestAddress, publicBase } from "../../testUsers";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const gate = await adminGate();
  if (!gate.ok) return gate.res;
  let email: unknown;
  try { email = ((await request.json()) as { email?: unknown }).email; } catch { /* empty */ }
  if (!isTestAddress(email)) {
    return NextResponse.json({ error: "Only an @example.test address can be signed in this way" }, { status: 400, headers: NO_STORE });
  }
  const address = email.trim().toLowerCase();
  const base = publicBase();
  const minted = base ? mintMagicToken(address) : null;
  if (!base || !minted || !minted.ok) {
    return NextResponse.json({ error: "Sign-in links are not configured on this instance" }, { status: 503, headers: NO_STORE });
  }
  // AUDITED: who minted a sign-in for which test address, and until when.
  console.info(`[terminal-auth] test-user sign-in link minted for ${address} by ${gate.email} (valid ${Math.round(ttlSeconds() / 60)} min, single use)`);
  return NextResponse.json({ url: `${base}/api/auth/redeem?t=${encodeURIComponent(minted.token)}&next=%2F`, minutes: Math.round(ttlSeconds() / 60) }, { headers: NO_STORE });
}
