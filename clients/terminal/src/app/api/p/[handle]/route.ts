/** `GET /api/p/<handle>` — a public profile page's data (D35): what the person chose to publish.
 *
 *  Like the invite preview, it goes to agent-api WITHOUT a user key: a public page renders for anyone.
 *  The handle is the capability, minted when the person switched the page on and dropped when they
 *  switch it off; agent-api answers only the fields they marked public, never an org field, and 404 for
 *  anything else. */
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
const NO_STORE = { "Cache-Control": "no-store" } as const;

export async function GET(_req: Request, ctx: { params: Promise<{ handle: string }> }) {
  const { handle } = await ctx.params;
  if (!/^[A-Za-z0-9_-]{16,64}$/.test(handle || "")) return NextResponse.json({ error: "not_found" }, { status: 404, headers: NO_STORE });
  const base = (process.env.AGENT_API_URL || "").replace(/\/$/, "");
  if (!base) return NextResponse.json({ error: "unconfigured" }, { status: 503, headers: NO_STORE });
  try {
    const upstream = await fetch(`${base}/api/profiles/public/${encodeURIComponent(handle)}`, { cache: "no-store" });
    return new NextResponse(await upstream.text(), { status: upstream.status, headers: { "Content-Type": "application/json", ...NO_STORE } });
  } catch {
    return NextResponse.json({ error: "upstream_unavailable" }, { status: 502, headers: NO_STORE });
  }
}
