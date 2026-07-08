/** Admin infra overview — the panel's ONE data fetch, proxied server-side to agent-api's
 *  internal-tier `GET /api/admin/overview` (runtime.v1 workloads + per-meeting pipeline
 *  carriers). The X-Internal-Secret never reaches the browser; non-admins get the same 404 as
 *  /api/admin/me. Read-only by construction — this route only ever GETs. */
import { NextResponse } from "next/server";
import { requireAdmin } from "../gate";

export const dynamic = "force-dynamic";

export async function GET() {
  const admin = await requireAdmin();
  if (!admin) return new NextResponse(null, { status: 404 });

  const agentApiUrl = (process.env.AGENT_API_URL || "http://127.0.0.1:18100").replace(/\/$/, "");
  try {
    const res = await fetch(`${agentApiUrl}/api/admin/overview`, {
      headers: { "X-Internal-Secret": process.env.VEXA_INTERNAL_API_SECRET || "" },
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
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
