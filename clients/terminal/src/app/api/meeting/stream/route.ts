/** SSE proxy — forwards the live meeting feed (transcript + copilot cards) from agent-api. */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const AGENT_API = process.env.AGENT_API_URL || "http://127.0.0.1:18100";

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
    const upstream = await fetch(`${AGENT_API}/api/meeting/stream${req.nextUrl.search}`, {
      method: "GET",
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
