/** Restore a bundle's workspace and notes page for the meeting just imported from it. The zip is
 *  streamed to the gateway untouched (the catch-all would re-encode it as text); the answer is JSON,
 *  passed through with its status so a refusal's code reaches the dialog. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../proxyAuth";
import { meetingsOnly } from "../../../mode";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export async function POST(req: NextRequest) {
  if (meetingsOnly()) return json(404, { error: "not_found", detail: "agent endpoints are disabled in meetings mode" });
  const id = req.nextUrl.searchParams.get("meeting_id") || "";
  if (!/^\d+$/.test(id)) return json(422, { detail: "meeting_id must be a number" });
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/meeting/bundle-restore?meeting_id=${id}`, {
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
    return json(502, { error: "upstream_unreachable", detail: err instanceof Error && err.message ? err.message : "upstream unreachable" });
  }
}
