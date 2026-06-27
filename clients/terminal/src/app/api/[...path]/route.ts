/** The ONE proxy — a single catch-all that forwards every /api/* call to the right backend,
 *  keeping hosts + keys server-side. It replaces the ~9 near-identical thin route files.
 *
 *  Path-based routing (the architecture seam — two domains behind one client API):
 *    • meetings · transcripts · bots  → the MEETINGS domain → the GATEWAY (GATEWAY_URL),
 *      authenticated with X-API-Key: VEXA_BOT_API_KEY. meeting-api owns these, fronted by the
 *      gateway at root paths (/meetings, /transcripts/{platform}/{native}, /bots).
 *    • everything else (chat · copilot · sessions · routines · workspace · events · …)
 *      → the AGENT domain → agent-api (AGENT_API_URL), no key, served under /api/*.
 *
 *  Carries through: the path after /api/, the query string, the request body, and the upstream
 *  status + JSON. SSE (/api/meeting/stream) and the workspace KG reader (/api/workspace/[...seg])
 *  stay as their own files — they need streaming / segment-specific shaping.
 */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../proxyAuth";

export const dynamic = "force-dynamic";

const AGENT_API = (process.env.AGENT_API_URL || "http://127.0.0.1:18100").replace(/\/$/, "");
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

const MEETINGS_DOMAIN = /^(meetings|transcripts|bots)(\/|$)/;

/** Resolve the upstream URL + headers for a captured /api/<path...> request.
 *  Meetings-domain calls carry the per-user X-API-Key (cookie token → VEXA_API_KEY → VEXA_BOT_API_KEY). */
async function upstreamFor(path: string, search: string): Promise<{ url: string; headers: HeadersInit }> {
  if (MEETINGS_DOMAIN.test(path)) {
    return { url: `${GATEWAY_URL}/${path}${search}`, headers: { "X-API-Key": await resolveApiKey() } };
  }
  return { url: `${AGENT_API}/api/${path}${search}`, headers: {} };
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
  } catch {
    // upstream offline — degrade to an empty-but-valid JSON body so the UI keeps last-known state
    return new Response(JSON.stringify({}), { status: 502, headers: { "Content-Type": "application/json" } });
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export const GET = (req: NextRequest, ctx: Ctx) => forward(req, ctx.params);
export const POST = (req: NextRequest, ctx: Ctx) => forward(req, ctx.params);
export const DELETE = (req: NextRequest, ctx: Ctx) => forward(req, ctx.params);
