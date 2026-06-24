// POST /api/meeting/stop — server-side proxy to agent-api's bot-stop (DELETE the gateway /bots/…).
// The per-meeting "Stop" button in the Meetings list posts a { native_id, platform } here.
import { NextRequest } from "next/server";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const r = await fetch(`${AGENT_API}/api/meeting/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  return new Response(await r.text(), {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}
