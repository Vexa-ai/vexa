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
 * Read webhook_deliveries from user data stored via admin API.
 */
export async function GET(request: NextRequest) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();

  if (!VEXA_ADMIN_API_KEY) {
    return NextResponse.json({
      deliveries: [],
      stats: { total: 0, delivered: 0, retrying: 0, failed: 0 },
    });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get("vexa-token")?.value;
  if (!token) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const userId = request.nextUrl.searchParams.get("userId");
  if (!userId) {
    return NextResponse.json({
      deliveries: [],
      stats: { total: 0, delivered: 0, retrying: 0, failed: 0 },
    });
  }

  try {
    const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
      headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
      cache: "no-store",
    });

    if (!response.ok) {
      return NextResponse.json({
        deliveries: [],
        stats: { total: 0, delivered: 0, retrying: 0, failed: 0 },
      });
    }

    const userData = await response.json();
    const data = userData.data || {};
    const allDeliveries: Array<Record<string, unknown>> = data.webhook_deliveries || [];

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
