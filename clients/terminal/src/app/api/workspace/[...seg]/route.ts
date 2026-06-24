/** Read proxy for the workspace knowledge graph → agent-api /api/workspace/* (host stays server-side). */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function GET(req: NextRequest, ctx: { params: Promise<{ seg: string[] }> }) {
  const { seg } = await ctx.params;
  const upstream = await fetch(`${AGENT_API}/api/workspace/${seg.join("/")}${req.nextUrl.search}`);
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
