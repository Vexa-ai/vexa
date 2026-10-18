/** The agent domain's half of a meeting export: the bound workspace's tree and the meeting's page,
 *  as a zip. Its own route because the catch-all decodes every answer as text and relabels it JSON —
 *  fatal for a zip. Meetings-only mode refuses it like every agent path (404), and the Export action
 *  then exports the meeting without those parts and says so. */
import type { NextRequest } from "next/server";
import { resolveApiKey } from "../../proxyAuth";
import { meetingsOnly } from "../../../mode";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
const PASSTHROUGH = ["content-type", "content-length", "x-vexa-workspace-files", "x-vexa-notes-page", "x-vexa-skipped-files"] as const;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export async function GET(req: NextRequest) {
  if (meetingsOnly()) return json(404, { error: "not_found", detail: "agent endpoints are disabled in meetings mode" });
  const id = req.nextUrl.searchParams.get("meeting_id") || "";
  if (!/^\d+$/.test(id)) return json(422, { detail: "meeting_id must be a number" });
  try {
    const apiKey = await resolveApiKey();
    const upstream = await fetch(`${GATEWAY_URL}/agent/meeting/bundle-parts?meeting_id=${id}`, {
      headers: apiKey ? { "X-API-Key": apiKey } : {}, cache: "no-store",
    });
    const headers = new Headers({ "Cache-Control": "private, no-store" });
    for (const k of PASSTHROUGH) { const v = upstream.headers.get(k); if (v) headers.set(k, v); }
    if (upstream.status === 204) return new Response(null, { status: 204, headers });
    if (!upstream.ok) {
      return new Response(await upstream.text(), { status: upstream.status, headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" } });
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (err) {
    return json(502, { error: "upstream_unreachable", detail: err instanceof Error && err.message ? err.message : "upstream unreachable" });
  }
}
