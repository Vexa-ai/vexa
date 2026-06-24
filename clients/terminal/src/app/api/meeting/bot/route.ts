// POST /api/meeting/bot — server-side proxy to agent-api's bot-launch (keeps the backend host
// server-side, P6). The "add bot from URL" box in the Meetings list posts a { url } here.
import { NextRequest } from "next/server";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const r = await fetch(`${AGENT_API}/api/meeting/bot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  return new Response(await r.text(), {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}
