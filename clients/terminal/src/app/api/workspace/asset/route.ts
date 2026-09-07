/** Proxy for workspace ASSETS — the bytes a page's `![…](assets/…)` renders (Vexa-ai/vexa#1612).
 *
 *  It exists as its OWN route rather than a case inside `../[...seg]/route.ts` because that handler
 *  reads every upstream answer with `await upstream.text()` and stamps `Content-Type:
 *  application/json` on it. For JSON that is correct and deliberate; for a PNG it is data loss —
 *  the bytes come back through a UTF-8 decode, every byte that is not valid UTF-8 becomes U+FFFD,
 *  and the browser is then told the replacement characters are JSON. A static segment wins over the
 *  sibling catch-all in the App Router, so this file takes `/api/workspace/asset` whole — which
 *  means every method that path needs must be exported HERE (GET reads, POST fetches one in).
 *
 *  The upstream's head is carried through, not re-minted: content-type, the cache validators and
 *  the two headers that decide what the browser does with an `.svg` (`nosniff`, and the
 *  `default-src 'none'` sandbox) are agent-api's answers about ITS file, and a proxy that rewrites
 *  them is answering a question it cannot see. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../proxyAuth";
import { meetingsOnly } from "../../../mode";

export const dynamic = "force-dynamic";

// One authenticated edge: workspace reads go through the gateway (which injects X-User-Id).
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

/** The headers an asset answer is only correct WITH. Content-Length is deliberately absent —
 *  the runtime recomputes it, and a stale one truncates the image. */
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

const unreachable = () =>
  new Response(JSON.stringify({ error: "upstream_unavailable" }), {
    status: 502, headers: { "Content-Type": "application/json" },
  });

/** READ one asset. The body is streamed through as bytes and never decoded. */
export async function GET(req: NextRequest) {
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/workspace/asset${req.nextUrl.search}`, {
      headers: {
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
        // the conditional request the <img> makes on a re-render — without it every page render
        // re-downloads every picture on it
        ...(req.headers.get("if-none-match") ? { "If-None-Match": req.headers.get("if-none-match")! } : {}),
      },
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
    console.error("[terminal-api] workspace asset proxy failed", err);
    return unreachable();
  }
}

/** The two WRITES on this path: POST fetches a remote image in, PUT stores one a person dropped,
 *  pasted or attached. Both are writes, so meetings-only mode refuses them exactly as the sibling
 *  catch-all refuses POST — and both answer JSON, so the body is read as text here. */
async function write(req: NextRequest, method: "POST" | "PUT") {
  if (meetingsOnly()) {
    return new Response(JSON.stringify({ error: "not_found", detail: "agent endpoints are disabled in meetings mode" }),
      { status: 404, headers: { "Content-Type": "application/json" } });
  }
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/workspace/asset${req.nextUrl.search}`, {
      method,
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
    console.error("[terminal-api] workspace asset write proxy failed", err);
    return unreachable();
  }
}

export const POST = (req: NextRequest) => write(req, "POST");
export const PUT = (req: NextRequest) => write(req, "PUT");
