/** Routine delete proxy → agent-api DELETE /api/routines/{id}. */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

export async function DELETE(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const upstream = await fetch(`${AGENT_API}/api/routines/${id}${req.nextUrl.search}`, { method: "DELETE" });
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
