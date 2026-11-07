/** Runtime deployment config for the browser — read at REQUEST time, not build time.
 *
 *  Next.js inlines `NEXT_PUBLIC_*` at build, so a per-deployment default baked into the image can't
 *  be changed without a rebuild. This endpoint exposes the values the terminal reads at runtime from
 *  the container env, so a plain compose var (no rebuild) takes effect. Currently: `DEFAULT_BOT_NAME`
 *  — the meeting bot's display name the terminal sends on join (see surfaces/defaultBotName.ts) —
 *  and whether this deployment can create a Google Meet (see ../googleMeet.ts), which is what
 *  decides which of the empty chat's two standing acts is offered (Vexa-ai/vexa#1614).
 */
import { NextResponse } from "next/server";
import { googleMeetConfigured, googleSignInConfigured } from "../googleMeet";

export const dynamic = "force-dynamic"; // never cache — reflect the live container env per request

export async function GET() {
  return NextResponse.json(
    {
      defaultBotName: process.env.DEFAULT_BOT_NAME?.trim() || null,
      google: { signIn: googleSignInConfigured(), meet: googleMeetConfigured() },
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
