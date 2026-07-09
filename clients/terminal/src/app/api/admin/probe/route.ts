/** Admin golden smoke probe — proxied server-side to agent-api's internal-tier
 *  `POST /api/admin/probe` (gateway → meeting-api → runtime → redis carriers → transcript relay).
 *  Same gate + 404 semantics as the other /api/admin routes. */
import { NextResponse } from "next/server";
import { requireAdmin } from "../gate";

export const dynamic = "force-dynamic";

export async function POST() {
  const admin = await requireAdmin();
  if (!admin) return new NextResponse(null, { status: 404 });

  const agentApiUrl = (process.env.AGENT_API_URL || "http://127.0.0.1:18100").replace(/\/$/, "");
  try {
    const res = await fetch(`${agentApiUrl}/api/admin/probe`, {
      method: "POST",
      headers: { "X-Internal-Secret": process.env.VEXA_INTERNAL_API_SECRET || "" },
      cache: "no-store",
      signal: AbortSignal.timeout(30000),
    });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch (err) {
    return NextResponse.json(
      { error: `agent-api unreachable: ${(err as Error).message}` },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
