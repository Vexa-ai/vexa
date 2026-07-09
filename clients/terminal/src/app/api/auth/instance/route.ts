/** Instance status for the login surface — UNAUTHENTICATED by design: the sign-in screen needs
 *  to know, before any identity exists, whether to show the one-time "set up your instance"
 *  admin-claim variant. Exposes ONLY {admin_exists} — a boolean a visitor could infer anyway
 *  (a claim screen is showing or it isn't); the internal secret stays server-side. Providers
 *  are NOT repeated here — the client already discovers them via /api/auth/providers.
 */
import { NextResponse } from "next/server";
import { instanceHasAdmin } from "../adminApi";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(
    { admin_exists: await instanceHasAdmin() },
    { headers: { "Cache-Control": "no-store" } },
  );
}
