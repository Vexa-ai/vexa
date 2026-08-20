/**
 *  Streaming proxy for a recording's master media bytes (the `<video>`/`<audio>` src).
 *
 *  A FLAT route (query params `rec`, `mf`, `type`) rather than the backend's nested
 *  `/recordings/{id}/media/{fid}/raw` path — the generic `/api/[...path]` proxy can't serve it (it
 *  buffers the body as text and forces `application/json`, which corrupts video), and mirroring the
 *  nested path here would spawn several README-less route dirs (gate:readme). This route forwards the
 *  HTTP `Range` header and passes the upstream status + headers + BODY STREAM through verbatim, so a
 *  `<video>` element can seek. Auth (`X-API-Key`) is injected server-side — the browser never sees it.
 */
import type { NextRequest } from "next/server";

import { resolveApiKey } from "../proxyAuth";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

export async function GET(req: NextRequest): Promise<Response> {
  const q = req.nextUrl.searchParams;
  const rec = q.get("rec");
  const mf = q.get("mf");
  const type = q.get("type") || "combined";
  if (!rec || !mf) {
    return new Response(JSON.stringify({ error: "bad_request", detail: "rec and mf are required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  const url = `${GATEWAY_URL}/recordings/${encodeURIComponent(rec)}/media/${encodeURIComponent(mf)}/raw?type=${encodeURIComponent(type)}`;
  const headers: Record<string, string> = { "X-API-Key": await resolveApiKey() };
  const range = req.headers.get("range");
  if (range) headers["Range"] = range; // seek support → upstream answers 206 with Content-Range

  try {
    const upstream = await fetch(url, { method: "GET", headers, cache: "no-store" });
    const out = new Headers();
    for (const h of ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"]) {
      const v = upstream.headers.get(h);
      if (v) out.set(h, v);
    }
    out.set("Cache-Control", "no-store");
    // Stream the body (upstream.body), never .text() — that would buffer + corrupt binary.
    return new Response(upstream.body, { status: upstream.status, headers: out });
  } catch (err) {
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return new Response(JSON.stringify({ error: "upstream_unreachable", detail }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
