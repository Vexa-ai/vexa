/** Proxy for a chat message's ATTACHMENT — the thumbnail in a bubble and the picture its lightbox
 *  opens (agent-api `GET /api/sessions/{session}/attachment`).
 *
 *  Its own route for the reason `../../../workspace/asset/route.ts` is: the catch-all reads every
 *  answer as text and labels it JSON, which corrupts an image. The answer is streamed through as
 *  bytes with agent-api's own headers. WHO may read it is agent-api's decision — the conversation's
 *  participants — made on the per-user key this proxy forwards; nothing here widens it. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../../proxyAuth";
import { meetingsOnly } from "../../../../../platform/mode";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

const PASSTHROUGH = [
  "content-type", "etag", "cache-control", "content-disposition",
  "x-content-type-options", "content-security-policy",
] as const;

function head(upstream: Response): Headers {
  const out = new Headers();
  for (const k of PASSTHROUGH) {
    const v = upstream.headers.get(k);
    if (v) out.set(k, v);
  }
  return out;
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ session: string }> }) {
  if (meetingsOnly()) {
    return new Response(JSON.stringify({ error: "not_found" }), { status: 404, headers: { "Content-Type": "application/json" } });
  }
  const { session } = await ctx.params;
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(
      `${GATEWAY_URL}/agent/sessions/${encodeURIComponent(session)}/attachment${req.nextUrl.search}`, {
        headers: {
          ...(apiKey ? { "X-API-Key": apiKey } : {}),
          ...(req.headers.get("if-none-match") ? { "If-None-Match": req.headers.get("if-none-match")! } : {}),
        },
        cache: "no-store",
      });
    if (upstream.status === 304) return new Response(null, { status: 304, headers: head(upstream) });
    if (!upstream.ok) {
      return new Response(await upstream.text(), {
        status: upstream.status,
        headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
      });
    }
    return new Response(upstream.body, { status: upstream.status, headers: head(upstream) });
  } catch (err) {
    console.error("[terminal-api] attachment proxy failed", err);
    return new Response(JSON.stringify({ error: "upstream_unavailable" }), {
      status: 502, headers: { "Content-Type": "application/json" },
    });
  }
}
