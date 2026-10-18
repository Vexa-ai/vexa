/** Instance status for the login surface — UNAUTHENTICATED by design: the sign-in screen needs
 *  to know, before any identity exists, one thing it cannot ask an authenticated edge for.
 *
 *  Exposes exactly ONE BOOLEAN, which a visitor infers from the screen anyway:
 *    • `admin_exists` — is a claim screen showing or isn't it.
 *
 *  It used to carry `global_setup` too (the company-layer gate, founder ruling 2026-09-02). That
 *  gate is gone (founder ruling 2026-10-08: "let's remove global setup at all so that there is no
 *  need to setup global at all - let it be empty with no data - it's fine"), and so is the field.
 *  Nothing else crosses: in particular the company name, which would identify a customer to anyone
 *  who curls an anonymous endpoint on a self-hosted box.
 *
 *  The internal secret stays server-side. Providers are NOT repeated here — the client already
 *  discovers them via /api/auth/providers.
 */
import { NextResponse } from "next/server";
import { instanceState } from "../adminApi";

export const dynamic = "force-dynamic";

export async function GET() {
  const state = await instanceState();
  return NextResponse.json({ admin_exists: state.admin_exists }, { headers: { "Cache-Control": "no-store" } });
}
