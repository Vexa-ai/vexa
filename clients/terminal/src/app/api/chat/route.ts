/** SSE proxy for the agent chat turn — POST /api/chat streams the agent's reply from agent-api.
 *
 *  Chat is SSE, like /api/meeting/stream — it needs the streaming + abort lifecycle, NOT the generic
 *  [...path] JSON proxy (which buffers and, under the dev server, fails to load for this POST route).
 *  So it lives as its own route: own the downstream controller, close on upstream end, error on drop,
 *  and abort the upstream fetch when the client disconnects. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../proxyAuth";
import { meetingsOnly } from "../../mode";

export const dynamic = "force-dynamic";

// One authenticated edge: chat streams through the gateway (which injects X-User-Id), not agent-api directly.
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  "X-Accel-Buffering": "no",
} as const;

/** A fault as the chat stream carries it (`surfaces/faults.ts` renders it): WHO failed and HOW. */
type ChatFault = { source: string; kind: string; status?: number | null; detail?: string; remedy?: string };

function sseError(message: string, status?: number, fault?: ChatFault) {
  return new Response(`data: ${JSON.stringify({ type: "error", message, status, ...(fault ? { fault } : {}) })}\n\n`, {
    status: 200,
    headers: SSE_HEADERS,
  });
}

/** THE TYPED FAULT agent-api answered with, when it answered with one (P18): `{detail, fault}`. */
function typedRefusal(body: string): { detail: string; fault: ChatFault } | null {
  try {
    const b = JSON.parse(body) as { detail?: unknown; fault?: { source?: unknown; kind?: unknown } } | null;
    const f = b?.fault;
    if (!f || typeof f.source !== "string" || typeof f.kind !== "string") return null;
    return { detail: typeof b?.detail === "string" ? b.detail : "", fault: f as ChatFault };
  } catch {
    return null;
  }
}

/** THE PROXY'S FLOOR (P18). A 5xx with no typed fault in it — an agent-api one release behind, a
 *  gateway answering for it — still names WHO did not answer. A bare "Internal Server Error" is the
 *  exact text the founder was shown for a runtime that refused his agent, and it named nobody. */
function upstreamFault(status: number, said = ""): ChatFault {
  const down = status === 502 || status === 503 || status === 504;
  return {
    source: "agent-api",
    kind: down ? "unavailable" : "internal",
    status,
    // A sentence the service wrote for the person (its own `detail`) is kept: it knows more than
    // this floor does. Only a body that says nothing — or says "Internal Server Error" — is replaced.
    detail: said || (down ? "the agent service did not answer" : "the agent service failed while taking this message"),
    remedy: said ? "" : "Send it again in a moment; if it keeps happening, an operator should check agent-api's log.",
  };
}

/** The prose `detail` of a JSON error body, or "" — never the framework's bare status phrase. */
function proseDetail(body: string): string {
  try {
    const d = (JSON.parse(body) as { detail?: unknown } | null)?.detail;
    return typeof d === "string" && d.trim() && !/^internal server error$/i.test(d.trim()) ? d.trim() : "";
  } catch {
    return "";
  }
}

const GATEWAY_UNREACHABLE: ChatFault = {
  source: "gateway", kind: "unreachable", status: null,
  detail: "the terminal could not reach the Vexa gateway",
  remedy: "Send it again in a moment; if it persists, the gateway is down.",
};

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
  // Meetings-only mode: chat is an agent surface — refused at the edge like the catch-all's agent branch.
  if (meetingsOnly()) {
    return new Response(JSON.stringify({ error: "not_found", detail: "agent endpoints are disabled in meetings mode" }), { status: 404, headers: { "Content-Type": "application/json" } });
  }
  const abort = new AbortController();
  const onClientGone = () => abort.abort();
  req.signal.addEventListener("abort", onClientGone);

  try {
    const body = await req.text();
    const apiKey = await resolveApiKey();
    // Forward Last-Event-ID so a reconnect RESUMES the chat turn from the client's last-seen cursor
    // (gapless), instead of re-dispatching or missing everything the worker emitted during the gap —
    // the same resume contract as /api/meeting/stream. On resume agent-api re-attaches to the warm
    // unit and reads from the cursor; it does NOT start a second turn.
    const lastEventId = req.headers.get("last-event-id");
    const upstream = await fetch(`${GATEWAY_URL}/agent/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      },
      body,
      signal: abort.signal,
    });
    if (!upstream.ok) {
      const raw = await upstream.text().catch(() => "");
      req.signal.removeEventListener("abort", onClientGone);
      const typed = typedRefusal(raw);
      if (typed) return sseError(typed.detail || `agent-api chat returned ${upstream.status}`, upstream.status, typed.fault);
      if (upstream.status >= 500) {
        const said = proseDetail(raw);
        const fault = upstreamFault(upstream.status, said);
        return sseError(said || `${fault.detail}.`, upstream.status, fault);
      }
      const detail = raw.trim().replace(/\s+/g, " ");
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
    return sseError(`${GATEWAY_UNREACHABLE.detail}.`, 502, GATEWAY_UNREACHABLE);
  }
}
