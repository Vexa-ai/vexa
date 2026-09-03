/** Instance status for the login surface — UNAUTHENTICATED by design: the sign-in screen needs
 *  to know, before any identity exists, two things it cannot ask an authenticated edge for.
 *
 *  Exposes exactly TWO BOOLEAN-SHAPED FACTS, both of which a visitor infers from the screen anyway:
 *    • `admin_exists` — is a claim screen showing or isn't it,
 *    • `global_setup` — is this instance still being set up by its administrator (the company-layer
 *      gate, founder ruling 2026-09-02). The visitor is about to be told this in a sentence; the
 *      field only decides which sentence.
 *
 *  WHAT IS DELIBERATELY WITHHELD: `company`. instanceState() reads it, and this route drops it. The
 *  company name is the one field that is not already on the screen and not already inferable — it is
 *  the identity of a customer, leaking from an anonymous endpoint on a self-hosted box that may sit
 *  on the public internet, to anyone who curls it. It buys the sign-in card nothing (the card says
 *  "its administrator", not "Acme's administrator"), so it does not cross. The name IS shown after
 *  sign-in, on the admin's own setup card, which reads it from the authenticated /api/global/state.
 *
 *  The internal secret stays server-side. Providers are NOT repeated here — the client already
 *  discovers them via /api/auth/providers.
 */
import { NextResponse } from "next/server";
import { instanceState } from "../adminApi";

export const dynamic = "force-dynamic";

export async function GET() {
  const state = await instanceState();
  return NextResponse.json(
    { admin_exists: state.admin_exists, global_setup: state.global_setup },
    { headers: { "Cache-Control": "no-store" } },
  );
}
