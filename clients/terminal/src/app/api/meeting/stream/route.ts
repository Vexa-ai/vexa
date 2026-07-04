/** SSE proxy — forwards the live meeting feed (transcript + copilot cards) from agent-api. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../proxyAuth";

export const dynamic = "force-dynamic";

// One authenticated edge: the live feed streams through the gateway (which injects X-User-Id), not agent-api directly.
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  "X-Accel-Buffering": "no",
} as const;

function sseError(message: string, status?: number) {
  return new Response(`data: ${JSON.stringify({ type: "stream-error", message, status })}\n\n`, {
    status: 200,
    headers: SSE_HEADERS,
  });
}

/**
 * Pump an upstream SSE body into a fresh downstream ReadableStream, propagating
 * end-of-stream and failures to the browser.
 *
 * The previous implementation returned `upstream.body` directly. When agent-api
 * restarted/dropped, the upstream reader would end (or error), but the downstream
 * stream the browser was reading from was never closed/errored, so the EventSource
 * stayed half-open: no `onerror`, no native reconnect, for minutes. Here we own the
 * downstream controller and explicitly:
 *   - `controller.close()` when the upstream reader reports `done`, and
 *   - `controller.error()` when the upstream read throws (network drop / abort),
 * either of which gives the browser EventSource a clean disconnect that triggers
 * its native reconnect. We also abort the upstream fetch when the client goes away
 * (downstream `cancel`), so no upstream connection is leaked.
 */
function proxyStream(upstreamBody: ReadableStream<Uint8Array>, abort: AbortController): ReadableStream<Uint8Array> {
  const reader = upstreamBody.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        if (done) {
          // Upstream ended: close the downstream so the browser sees the disconnect.
          controller.close();
          return;
        }
        controller.enqueue(value);
      } catch (err) {
        // Upstream network error / abort: error the downstream so the browser
        // EventSource fires onerror and reconnects, instead of hanging half-open.
        controller.error(err);
      }
    },
    cancel(reason) {
      // Browser disconnected (tab closed, EventSource.close, navigation): tear down
      // the upstream fetch so we don't leak the connection to agent-api.
      abort.abort(reason);
      reader.cancel(reason).catch(() => {});
    },
  });
}

export async function GET(req: NextRequest) {
  // Tie the upstream fetch lifetime to this request. Aborting also unblocks an
  // in-flight reader.read(), so the pump's catch path closes the downstream.
  const abort = new AbortController();
  const onClientGone = () => abort.abort();
  req.signal.addEventListener("abort", onClientGone);

  try {
    const apiKey = await resolveApiKey();
    const headers: Record<string, string> = apiKey ? { "X-API-Key": apiKey } : {};
    // Forward Last-Event-ID so the live feed RESUMES from the client's last-seen segment after a
    // reconnect (gapless). Without it, a transient disconnect dropped every segment published in the
    // gap from the live view — the real-time transcript-loss bug. The browser EventSource sets this
    // automatically once the upstream emits `id:` lines (agent-api meeting_stream does).
    // Last-Event-ID arrives EITHER as the header (browser-native auto-reconnect) OR as the `lid` query
    // param (the engine's manual forceReconnect, which opens a fresh EventSource that drops the header).
    const lastEventId = req.headers.get("last-event-id") || req.nextUrl.searchParams.get("lid");
    if (lastEventId) headers["Last-Event-ID"] = lastEventId;
    const upstream = await fetch(`${GATEWAY_URL}/agent/meeting/stream${req.nextUrl.search}`, {
      method: "GET",
      headers,
      signal: abort.signal,
    });
    if (!upstream.ok) {
      const detail = (await upstream.text().catch(() => "")).trim().replace(/\s+/g, " ");
      req.signal.removeEventListener("abort", onClientGone);
      return sseError(detail || `agent-api stream returned ${upstream.status}`, upstream.status);
    }
    if (!upstream.body) {
      req.signal.removeEventListener("abort", onClientGone);
      return sseError("agent-api stream returned no body", 502);
    }
    return new Response(proxyStream(upstream.body, abort), {
      status: upstream.status,
      headers: SSE_HEADERS,
    });
  } catch (err) {
    req.signal.removeEventListener("abort", onClientGone);
    console.error("[terminal-api] meeting stream proxy failed", err);
    const message = err instanceof Error && err.message ? err.message : "upstream unavailable";
    return sseError(message, 502);
  }
}
