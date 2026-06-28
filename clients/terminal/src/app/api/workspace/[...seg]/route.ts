/** Read proxy for the workspace knowledge graph → agent-api /api/workspace/* (host stays server-side). */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../proxyAuth";

export const dynamic = "force-dynamic";

// One authenticated edge: workspace KG reads go through the gateway (which injects X-User-Id), not agent-api directly.
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

export async function GET(req: NextRequest, ctx: { params: Promise<{ seg: string[] }> }) {
  const { seg } = await ctx.params;
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/api/workspace/${seg.join("/")}${req.nextUrl.search}`, {
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
  const { seg } = await ctx.params;
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/api/workspace/${seg.join("/")}${req.nextUrl.search}`, {
      method: "POST",
      body: req.body,
      headers: {
        "Content-Type": req.headers.get("Content-Type") ?? "",
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
      },
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
    });
  } catch (err) {
    console.error("[terminal-api] workspace write proxy failed", err);
    return new Response(JSON.stringify({ error: "upstream_unavailable" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
