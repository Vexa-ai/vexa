/** Event ingress proxy → agent-api POST /events (an event.v1 Event → a unit.v1 Invocation → Dispatcher). */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function POST(req: NextRequest) {
  const upstream = await fetch(`${AGENT_API}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await req.text(),
  });
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
