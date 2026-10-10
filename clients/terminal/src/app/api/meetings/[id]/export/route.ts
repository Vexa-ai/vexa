/** Download a meeting as a meeting-bundle.v1 file.
 *
 *  Its own route because the catch-all decodes every answer as UTF-8 text and relabels it JSON —
 *  fatal for a zip. The bytes stream through untouched, with the upstream's Content-Type,
 *  Content-Length (the browser's progress bar) and Content-Disposition (the file name). A refusal
 *  (403 not the owner, 404, 409 still live) is JSON and passes through with its status.
 *
 *  POST is the same export with a PARTS archive as the body — the meeting's workspace tree and page
 *  from `/api/meeting/bundle-parts` — which meeting-api places inside the bundle. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../../proxyAuth";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
const PASSTHROUGH = ["content-type", "content-length", "content-disposition", "x-content-type-options"] as const;

async function exportFrom(req: NextRequest, id: string, method: "GET" | "POST") {
  if (!/^\d+$/.test(id)) {
    return new Response(JSON.stringify({ detail: "meeting id must be a number" }), {
      status: 422, headers: { "Content-Type": "application/json" },
    });
  }
  const media = req.nextUrl.searchParams.get("media") === "false" ? "?media=false" : "";
  try {
    const apiKey = await resolveApiKey();
    const init: RequestInit & { duplex?: "half" } = {
      method,
      headers: { ...(apiKey ? { "X-API-Key": apiKey } : {}), ...(method === "POST" ? { "Content-Type": "application/zip" } : {}) },
      cache: "no-store",
    };
    if (method === "POST") { init.body = req.body; init.duplex = "half"; }
    const upstream = await fetch(`${GATEWAY_URL}/meetings/${id}/export${media}`, init);
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

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return exportFrom(req, (await params).id, "GET");
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return exportFrom(req, (await params).id, "POST");
}
