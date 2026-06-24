/** Proxy — the active live-meeting copilots (agent-api's live registry). The meetings list polls this. */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function GET(_req: NextRequest) {
  try {
    const upstream = await fetch(`${AGENT_API}/api/meetings/live`, { cache: "no-store" });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-cache" },
    });
  } catch {
    return new Response(JSON.stringify({ meetings: [] }), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  }
}
