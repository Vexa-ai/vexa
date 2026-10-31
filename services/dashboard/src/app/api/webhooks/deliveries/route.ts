import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";
import { getAuthenticatedUserId } from "@/lib/auth-utils";
import { fetchLedgerRows, mapLedgerRow } from "@/lib/webhook-ledger";

const getAdminConfig = () => {
  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL || "";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";
  return { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY };
};

/**
 * GET /api/webhooks/deliveries?status=...&time_range=...
 *
 * Combines two sources (#841):
 * 1. The core's delivery ledger, GET /user/webhook/deliveries, read with the signed-in
 *    user's own token — every real delivery outcome, tagged `source: "ledger"`.
 * 2. user.data.webhook_deliveries — test deliveries sent from the dashboard's Test button,
 *    read for the signed-in user only, tagged `source: "test"`.
 * A `userId` query parameter is ignored; the session is the only identity.
 * When the ledger cannot be read the response still carries the Test rows plus
 * `ledger_unavailable: true`, so a failure never reads as "nothing was delivered".
 */
export async function GET(request: NextRequest) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) {
    return NextResponse.json({ error: "VEXA_API_URL is required" }, { status: 500 });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get(getAuthCookieName())?.value;
  if (!token) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  // Resolve the user from the session, never from the query string.
  const authenticatedUserId = await getAuthenticatedUserId();
  const userId =
    authenticatedUserId && /^\d+$/.test(authenticatedUserId) ? authenticatedUserId : null;

  const allDeliveries: Array<Record<string, unknown>> = [];

  // Source 1: the core's per-user delivery ledger (owner-scoped by the gateway).
  const ledger = await fetchLedgerRows(VEXA_API_URL, token, 100);
  ledger.rows.forEach((row, idx) => allDeliveries.push(mapLedgerRow(row, idx)));

  // Source 2: Test webhook deliveries from user data (the dashboard's own store).
  if (VEXA_ADMIN_API_KEY && userId) {
    try {
      const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${encodeURIComponent(userId)}`, {
        headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
        cache: "no-store",
      });
      if (userRes.ok) {
        const userData = await userRes.json();
        const testDeliveries: Array<Record<string, unknown>> = userData.data?.webhook_deliveries || [];
        for (const d of testDeliveries) allDeliveries.push({ ...d, source: "test" });
      }
    } catch {}
  }

  // Apply time range filter
  const timeRange = request.nextUrl.searchParams.get("time_range") || "7d";
  const now = Date.now();
  const rangeMs: Record<string, number> = {
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
  };
  const cutoff = now - (rangeMs[timeRange] || rangeMs["7d"]);
  let deliveries = allDeliveries.filter(
    (d) => new Date(d.created_at as string).getTime() >= cutoff
  );

  // Apply status filter
  const statusFilter = request.nextUrl.searchParams.get("status");
  if (statusFilter && statusFilter !== "all") {
    deliveries = deliveries.filter((d) => d.status === statusFilter);
  }

  // Sort by most recent first
  deliveries.sort(
    (a, b) =>
      new Date(b.created_at as string).getTime() -
      new Date(a.created_at as string).getTime()
  );

  // Compute stats (suppressed rows count toward the total only)
  const stats = {
    total: deliveries.length,
    delivered: deliveries.filter((d) => d.status === "delivered").length,
    retrying: deliveries.filter((d) => d.status === "retrying").length,
    failed: deliveries.filter((d) => d.status === "failed").length,
  };

  return NextResponse.json({ deliveries, stats, ledger_unavailable: !ledger.ok });
}
