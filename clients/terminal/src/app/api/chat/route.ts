/** SSE proxy for the agent chat turn — POST /api/chat streams the agent's reply from agent-api.
 *
 *  Chat is SSE, like /api/meeting/stream — it needs the streaming + abort lifecycle, NOT the generic
 *  [...path] JSON proxy (which buffers and, under the dev server, fails to load for this POST route).
 *  So it lives as its own route: own the downstream controller, close on upstream end, error on drop,
 *  and abort the upstream fetch when the client disconnects. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../proxyAuth";

export const dynamic = "force-dynamic";

// One authenticated edge: chat streams through the gateway (which injects X-User-Id), not agent-api directly.
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  "X-Accel-Buffering": "no",
} as const;

function sseError(message: string, status?: number) {
  return new Response(`data: ${JSON.stringify({ type: "error", message, status })}\n\n`, {
    status: 200,
    headers: SSE_HEADERS,
  });
}

/** Pump an upstream SSE body into a fresh downstream stream: close on done, error on throw, and abort
 *  the upstream fetch when the browser disconnects (so no agent-api connection is leaked). */
function proxyStream(upstreamBody: ReadableStream<Uint8Array>, abort: AbortController): ReadableStream<Uint8Array> {
  const reader = upstreamBody.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        if (done) { controller.close(); return; }
        controller.enqueue(value);
      } catch (err) {
        controller.error(err);
      }
    },
    cancel(reason) {
      abort.abort(reason);
      reader.cancel(reason).catch(() => {});
    },
  });
}

export async function POST(req: NextRequest) {
  const abort = new AbortController();
  const onClientGone = () => abort.abort();
  req.signal.addEventListener("abort", onClientGone);

  try {
    const body = await req.text();
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(apiKey ? { "X-API-Key": apiKey } : {}) },
      body,
      signal: abort.signal,
    });
    if (!upstream.ok) {
      const detail = (await upstream.text().catch(() => "")).trim().replace(/\s+/g, " ");
      req.signal.removeEventListener("abort", onClientGone);
      return sseError(detail || `agent-api chat returned ${upstream.status}`, upstream.status);
    }
    if (!upstream.body) {
      req.signal.removeEventListener("abort", onClientGone);
      return sseError("agent-api chat returned no body", 502);
    }
    return new Response(proxyStream(upstream.body, abort), { status: upstream.status, headers: SSE_HEADERS });
  } catch (err) {
    req.signal.removeEventListener("abort", onClientGone);
    console.error("[terminal-api] chat proxy failed", err);
    const message = err instanceof Error && err.message ? err.message : "upstream unavailable";
    return sseError(message, 502);
  }
}
