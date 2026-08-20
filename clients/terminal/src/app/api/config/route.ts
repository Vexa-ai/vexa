/** Runtime deployment config for the browser — read at REQUEST time, not build time.
 *
 *  Next.js inlines `NEXT_PUBLIC_*` at build, so a per-deployment default baked into the image can't
 *  be changed without a rebuild. This endpoint exposes the values the terminal reads at runtime from
 *  the container env, so a plain compose var (no rebuild) takes effect. Currently: `DEFAULT_BOT_NAME`
 *  — the meeting bot's display name the terminal sends on join (see surfaces/defaultBotName.ts).
 */
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic"; // never cache — reflect the live container env per request

export async function GET() {
  return NextResponse.json(
    { defaultBotName: process.env.DEFAULT_BOT_NAME?.trim() || null },
    { headers: { "Cache-Control": "no-store" } },
  );
}
