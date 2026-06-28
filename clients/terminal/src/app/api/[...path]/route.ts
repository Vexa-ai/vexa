/** The ONE proxy — a single catch-all that forwards every /api/* call to the right backend,
 *  keeping hosts + keys server-side. It replaces the ~9 near-identical thin route files.
 *
 *  Path-based routing (the architecture seam — two domains behind ONE authenticated edge, the gateway):
 *    • meetings · transcripts · bots  → the gateway ROOT paths (/meetings, /transcripts/{…}, /bots),
 *      where meeting-api is fronted.
 *    • everything else (chat · sessions · routines · workspace · models · …) → the gateway's /api/*
 *      prefix, where agent-api is fronted.
 *  BOTH carry the per-user X-API-Key (cookie token → VEXA_API_KEY → VEXA_BOT_API_KEY). The gateway
 *  resolves it → user and injects X-User-Id downstream, so agent-api derives `subject` from identity
 *  (the client never sends one — P20 scope). The terminal never reaches agent-api directly.
 *
 *  Carries through: the path after /api/, the query string, the request body, and the upstream
 *  status + JSON. SSE (/api/chat, /api/meeting/stream) and the workspace KG reader (/api/workspace/[...seg])
 *  stay as their own files — they need streaming / segment-specific shaping (all → the gateway).
 */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../proxyAuth";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

// Two domains behind ONE authenticated edge (the gateway):
//   • meetings · transcripts · bots  → the gateway ROOT (/meetings, …) — meeting-api behind it.
//   • everything else (chat · sessions · routines · workspace · models · …) → the gateway's /api/*
//     prefix — agent-api behind it.
// BOTH carry the per-user X-API-Key; the gateway resolves it → user and injects X-User-Id downstream,
// so the client never sends a `subject` (scope is server-derived — P20). agent-api is never reached directly.
const MEETINGS_DOMAIN = /^(meetings|transcripts|bots)(\/|$)/;

/** Resolve the upstream URL + headers for a captured /api/<path...> request. Every call carries the
 *  per-user X-API-Key (cookie token → VEXA_API_KEY → VEXA_BOT_API_KEY) to the single gateway edge. */
async function upstreamFor(path: string, search: string): Promise<{ url: string; headers: HeadersInit }> {
  const base = MEETINGS_DOMAIN.test(path) ? `${GATEWAY_URL}/${path}` : `${GATEWAY_URL}/api/${path}`;
  return { url: `${base}${search}`, headers: { "X-API-Key": await resolveApiKey() } };
}

async function forward(req: NextRequest, params: Promise<{ path: string[] }>): Promise<Response> {
  const { path } = await params;
  const { url, headers } = await upstreamFor(path.join("/"), req.nextUrl.search);

  const init: RequestInit = { method: req.method, headers: { ...headers }, cache: "no-store" };
  if (req.method !== "GET" && req.method !== "DELETE") {
    const body = await req.text();
    if (body) {
      init.body = body;
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    }
  }

  try {
    const upstream = await fetch(url, init);
    const contentType = upstream.headers.get("Content-Type") || "";
    if (contentType.includes("text/event-stream")) {
      return new Response(upstream.body, {
        status: upstream.status,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
          "X-Accel-Buffering": "no",
        },
      });
    }

    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-cache" },
    });
  } catch (err) {
    // upstream unreachable (gateway down / DNS). FAIL-LOUD (P18): return a real error body + 502 so the
    // client surfaces "backend unreachable" — never a silent empty {} that masquerades as "no data".
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return new Response(JSON.stringify({ error: "upstream_unreachable", detail }), { status: 502, headers: { "Content-Type": "application/json" } });
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export const GET = (req: NextRequest, ctx: Ctx) => forward(req, ctx.params);
export const POST = (req: NextRequest, ctx: Ctx) => forward(req, ctx.params);
export const DELETE = (req: NextRequest, ctx: Ctx) => forward(req, ctx.params);
