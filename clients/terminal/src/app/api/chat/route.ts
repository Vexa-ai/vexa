/** SSE proxy — forwards the chat turn to agent-api and streams the events back (key/host stay server-side). */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const upstream = await fetch(`${AGENT_API}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    },
  });
}
