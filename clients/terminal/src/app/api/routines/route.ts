/** Routines proxy → agent-api /api/routines (list + create). Host stays server-side. */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function GET(req: NextRequest) {
  const upstream = await fetch(`${AGENT_API}/api/routines${req.nextUrl.search}`);
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}

export async function POST(req: NextRequest) {
  const upstream = await fetch(`${AGENT_API}/api/routines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await req.text(),
  });
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
