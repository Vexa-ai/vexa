/** Download a meeting as a meeting-bundle.v1 file.
 *
 *  Its own route because the catch-all decodes every answer as UTF-8 text and relabels it JSON —
 *  fatal for a zip. The bytes stream through untouched, with the upstream's Content-Type,
 *  Content-Length (the browser's progress bar) and Content-Disposition (the file name). A refusal
 *  (403 not the owner, 404, 409 still live) is JSON and passes through with its status. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../../proxyAuth";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
const PASSTHROUGH = ["content-type", "content-length", "content-disposition", "x-content-type-options"] as const;

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    return new Response(JSON.stringify({ detail: "meeting id must be a number" }), {
      status: 422, headers: { "Content-Type": "application/json" },
    });
  }
  const media = req.nextUrl.searchParams.get("media") === "false" ? "?media=false" : "";
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/meetings/${id}/export${media}`, {
      headers: apiKey ? { "X-API-Key": apiKey } : {},
      cache: "no-store",
    });
    if (!upstream.ok) {
      return new Response(await upstream.text(), {
        status: upstream.status,
        headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
      });
    }
    const headers = new Headers({ "Cache-Control": "private, no-store" });
    for (const k of PASSTHROUGH) {
      const v = upstream.headers.get(k);
      if (v) headers.set(k, v);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (err) {
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return new Response(JSON.stringify({ error: "upstream_unreachable", detail }), {
      status: 502, headers: { "Content-Type": "application/json" },
    });
  }
}
