/** Read proxy for the workspace knowledge graph → agent-api /api/workspace/* (host stays server-side). */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../proxyAuth";
import { meetingsOnly } from "../../../mode";

export const dynamic = "force-dynamic";

/** Meetings-only mode: the workspace KG is an agent surface — refused at the edge (404). */
function refusedResponse(): Response | null {
  if (!meetingsOnly()) return null;
  return new Response(JSON.stringify({ error: "not_found", detail: "agent endpoints are disabled in meetings mode" }), { status: 404, headers: { "Content-Type": "application/json" } });
}

// One authenticated edge: workspace KG reads go through the gateway (which injects X-User-Id), not agent-api directly.
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

async function forwardWorkspaceWrite(
  req: NextRequest,
  ctx: { params: Promise<{ seg: string[] }> },
  method: "POST" | "DELETE",
) {
  const refused = refusedResponse();
  if (refused) return refused;
  const { seg } = await ctx.params;
  try {
    const apiKey = await resolveApiKey();
    const headers: Record<string, string> = apiKey ? { "X-API-Key": apiKey } : {};
    const init: RequestInit & { duplex?: "half" } = { method, headers };

    if (method === "POST") {
      headers["Content-Type"] = req.headers.get("Content-Type") ?? "";
      init.body = req.body;
      init.duplex = "half";
    }

    const upstream = await fetch(`${GATEWAY_URL}/agent/workspace/${seg.join("/")}${req.nextUrl.search}`, init);
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
    });
  } catch (err) {
    console.error(`[terminal-api] workspace ${method.toLowerCase()} proxy failed`, err);
    return new Response(JSON.stringify({ error: "upstream_unavailable" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ seg: string[] }> }) {
  const refused = refusedResponse();
  if (refused) return refused;
  const { seg } = await ctx.params;
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/workspace/${seg.join("/")}${req.nextUrl.search}`, {
      headers: apiKey ? { "X-API-Key": apiKey } : {},
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error("[terminal-api] workspace read proxy failed", err);
    return new Response(JSON.stringify({ error: "upstream_unavailable" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ seg: string[] }> }) {
  return forwardWorkspaceWrite(req, ctx, "POST");
}

export async function DELETE(req: NextRequest, ctx: { params: Promise<{ seg: string[] }> }) {
  return forwardWorkspaceWrite(req, ctx, "DELETE");
}
