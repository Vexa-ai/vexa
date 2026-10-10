/** Import a meeting-bundle.v1 file — the zip a person dropped on "Import meeting".
 *
 *  Its own route because the catch-all reads request bodies as text and labels them JSON: a zip
 *  through that path arrives as U+FFFD soup and every hash in its manifest fails. The body is
 *  streamed to the gateway untouched; the answer is JSON either way (the preview with
 *  `?dry_run=true`, the new meeting, or a refusal `{detail: {code, detail}}`), passed through with
 *  its status so the dialog can show exactly what was refused. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../proxyAuth";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

export async function POST(req: NextRequest) {
  const dryRun = req.nextUrl.searchParams.get("dry_run") === "true";
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/meetings/import${dryRun ? "?dry_run=true" : ""}`, {
      method: "POST",
      body: req.body,
      headers: {
        "Content-Type": "application/zip",
        ...(req.headers.get("content-length") ? { "Content-Length": req.headers.get("content-length")! } : {}),
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
      },
      cache: "no-store",
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" },
    });
  } catch (err) {
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return new Response(JSON.stringify({ error: "upstream_unreachable", detail }), {
      status: 502, headers: { "Content-Type": "application/json" },
    });
  }
}
