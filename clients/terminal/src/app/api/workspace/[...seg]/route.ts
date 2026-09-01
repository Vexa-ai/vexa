/** Proxy for the workspace knowledge graph → agent-api /api/workspace/* (host stays server-side).
 *  Being the more specific route, it SHADOWS the catch-all at src/app/api/[...path]/route.ts for
 *  every /api/workspace/* path — so every method the workspace surface uses must be exported HERE
 *  (GET · POST · PUT · DELETE). A method missing from this file is a 405, not a fallthrough. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../proxyAuth";
import { meetingsOnly } from "../../../mode";

export const dynamic = "force-dynamic";

/** Meetings-only mode: workspace WRITES stay refused, but READS are allowed — composed
 *  deep-links (?view=file:...) open workspace pages beside a meeting in the MCP-first
 *  viewer shape, and a read-only page is not an agent surface. */
function refusedResponse(method = "GET"): Response | null {
  if (!meetingsOnly() || method === "GET") return null;
  return new Response(JSON.stringify({ error: "not_found", detail: "agent endpoints are disabled in meetings mode" }), { status: 404, headers: { "Content-Type": "application/json" } });
}

// One authenticated edge: workspace KG reads go through the gateway (which injects X-User-Id), not agent-api directly.
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

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
  const refused = refusedResponse("POST");
  if (refused) return refused;
  const { seg } = await ctx.params;
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/workspace/${seg.join("/")}${req.nextUrl.search}`, {
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

/** PUT — writing ONE doc (the pages panel's Edit → Save, `writeWorkspaceFile`). It has to be
 *  declared HERE, not left to the catch-all: this file is the more specific route, so it SHADOWS
 *  `src/app/api/[...path]/route.ts` for every `/api/workspace/*` path — and a method the shadowing
 *  file does not export is a 405, no matter that the shadowed one exports it. Every write reached
 *  the browser as "Could not save: /api/workspace/file → 405" until this existed.
 *
 *  A WRITE, so meetings-only mode refuses it exactly like POST — and authorization stays upstream:
 *  agent-api's own mount rules answer `_global` for a non-admin with 403, which this passes through
 *  untouched. The distinction is the point: 403 is "you may not", 405 was "there is no such door". */
export async function PUT(req: NextRequest, ctx: { params: Promise<{ seg: string[] }> }) {
  const refused = refusedResponse("PUT");
  if (refused) return refused;
  const { seg } = await ctx.params;
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/workspace/${seg.join("/")}${req.nextUrl.search}`, {
      method: "PUT",
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

/** DELETE — removing one of the caller's own workspaces (a Minutes room's final step). Same
 *  authenticated edge as the reads/writes; the backend refuses the baseline, so the seed slot
 *  cannot be deleted through here either. */
export async function DELETE(req: NextRequest, ctx: { params: Promise<{ seg: string[] }> }) {
  const refused = refusedResponse();
  if (refused) return refused;
  const { seg } = await ctx.params;
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/workspace/${seg.join("/")}${req.nextUrl.search}`, {
      method: "DELETE",
      headers: apiKey ? { "X-API-Key": apiKey } : {},
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
    });
  } catch (err) {
    console.error("[terminal-api] workspace delete proxy failed", err);
    return new Response(JSON.stringify({ error: "upstream_unavailable" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
