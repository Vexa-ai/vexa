/** GET /api/meeting/jitsi-hosts — the deployment's declared self-hosted Jitsi hostnames
 *  (VEXA_JITSI_HOSTS, comma-separated), so the client-side link parser recognizes the same
 *  hosts the server parsers do. Hostnames only — public, read-only, no secrets. */
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const hosts = (process.env.VEXA_JITSI_HOSTS || "")
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
  return NextResponse.json({ hosts }, {
    headers: { "Cache-Control": "no-store, no-cache, must-revalidate" },
  });
}
