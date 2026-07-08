/** Admin platform-settings editor — the Settings surface's GLOBAL defaults (models /
 *  transcription), proxied server-side to admin-api's internal-tier `/internal/settings/{key}`.
 *  Same gate + hiding as the infra panel: a VERIFIED allowlisted admin (../gate.ts) or a plain
 *  404, and the X-Internal-Secret never reaches the browser. Unlike /api/admin/overview this
 *  route WRITES (PUT) — the admin-api validates fields and masks nothing here (admin tier sees
 *  the stored values; the per-user endpoints are the masked ones). */
import { NextRequest, NextResponse } from "next/server";
import { requireAdmin } from "../../gate";

export const dynamic = "force-dynamic";

async function proxy(req: NextRequest, key: string, method: "GET" | "PUT") {
  const admin = await requireAdmin();
  if (!admin) return new NextResponse(null, { status: 404 });

  const adminApiUrl = (process.env.VEXA_ADMIN_API_URL || "").replace(/\/$/, "");
  const secret = process.env.VEXA_INTERNAL_API_SECRET || "";
  if (!adminApiUrl || !secret) {
    return NextResponse.json(
      { error: "Admin API is not configured (VEXA_ADMIN_API_URL / VEXA_INTERNAL_API_SECRET)" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const res = await fetch(`${adminApiUrl}/internal/settings/${encodeURIComponent(key)}`, {
      method,
      headers: { "X-Internal-Secret": secret, "Content-Type": "application/json" },
      body: method === "PUT" ? await req.text() : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch (err) {
    return NextResponse.json(
      { error: `admin-api unreachable: ${(err as Error).message}` },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ key: string }> }) {
  return proxy(req, (await ctx.params).key, "GET");
}

export async function PUT(req: NextRequest, ctx: { params: Promise<{ key: string }> }) {
  return proxy(req, (await ctx.params).key, "PUT");
}
