import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

const getAdminConfig = () => {
  const VEXA_ADMIN_API_URL =
    process.env.VEXA_ADMIN_API_URL ||
    process.env.VEXA_API_URL ||
    "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";
  return { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY };
};

/**
 * GET /api/webhooks/deliveries?userId=N&status=...&time_range=...
 *
 * Combines two sources:
 * 1. meeting.data.webhook_delivery — real deliveries from the gateway on meeting completion
 * 2. user.data.webhook_deliveries  — test deliveries sent from the dashboard
 */
export async function GET(request: NextRequest) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();
  const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:18056";

  const cookieStore = await cookies();
  const token = cookieStore.get("vexa-token")?.value;
  if (!token) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const userId = request.nextUrl.searchParams.get("userId");

  try {
    const allDeliveries: Array<Record<string, unknown>> = [];

    // Source 1: Real meeting webhook deliveries from gateway
    const meetingsRes = await fetch(`${VEXA_API_URL}/meetings`, {
      headers: { "X-API-Key": token },
      cache: "no-store",
    });
    if (meetingsRes.ok) {
      const meetingsData = await meetingsRes.json();
      const meetings = meetingsData.meetings || [];
      for (const m of meetings) {
        const wd = m.data?.webhook_delivery;
        if (wd && wd.url) {
          allDeliveries.push({
            id: `meeting-${m.id}`,
            event: "meeting.completed",
            meeting_id: String(m.id),
            meeting_name: m.data?.name || m.native_meeting_id || `Meeting ${m.id}`,
            status: wd.status === "delivered" ? "delivered" : wd.status === "queued" ? "retrying" : "failed",
            attempts: wd.attempts || 1,
            max_attempts: wd.attempts || 1,
            response_status: wd.status_code || null,
            response_time_ms: null,
            endpoint_url: wd.url,
            created_at: wd.delivered_at || wd.queued_at || wd.failed_at || m.updated_at,
            last_attempt_at: wd.delivered_at || wd.queued_at || wd.failed_at || m.updated_at,
          });
        }
      }
    }

    // Source 2: Test webhook deliveries from user data
    if (VEXA_ADMIN_API_KEY && userId) {
      const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
        headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
        cache: "no-store",
      });
      if (userRes.ok) {
        const userData = await userRes.json();
        const testDeliveries = userData.data?.webhook_deliveries || [];
        allDeliveries.push(...testDeliveries);
      }
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

    // Compute stats
    const stats = {
      total: deliveries.length,
      delivered: deliveries.filter((d) => d.status === "delivered").length,
      retrying: deliveries.filter((d) => d.status === "retrying").length,
      failed: deliveries.filter((d) => d.status === "failed").length,
    };

    return NextResponse.json({ deliveries, stats });
  } catch {
    return NextResponse.json({
      deliveries: [],
      stats: { total: 0, delivered: 0, retrying: 0, failed: 0 },
    });
  }
}
