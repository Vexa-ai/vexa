/** SSE proxy — forwards the live meeting feed (transcript + copilot cards) from agent-api. */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function GET(req: NextRequest) {
  try {
    const upstream = await fetch(`${AGENT_API}/api/meeting/stream${req.nextUrl.search}`, { method: "GET" });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (err) {
    console.error("[terminal-api] meeting stream proxy failed", err);
    return new Response("event: error\ndata: {\"error\":\"upstream_unavailable\"}\n\n", {
      status: 502,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
    });
  }
}
