import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";
import { fetchLedgerRows } from "@/lib/webhook-ledger";

/**
 * GET /api/webhooks/deliveries/:meetingId
 *
 * Webhook delivery attempts for one meeting, read from the core's per-user delivery
 * ledger (GET /user/webhook/deliveries) with the signed-in user's own token and filtered
 * by `meeting_id` (#841). Requests without a user session are refused.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ meetingId: string }> }
) {
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) {
    return NextResponse.json({ error: "VEXA_API_URL is required" }, { status: 500 });
  }
  const cookieStore = await cookies();
  const userToken = cookieStore.get(getAuthCookieName())?.value;
  if (!userToken) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }
  const { meetingId } = await params;

  const ledger = await fetchLedgerRows(VEXA_API_URL, userToken, 100);
  const attempts = ledger.rows
    .filter((row) => String(row.meeting_id ?? "") === String(meetingId))
    .map((row) => ({
      attempt: (typeof row.attempt === "number" ? row.attempt : 0) + 1,
      timestamp: row.created_at || new Date(0).toISOString(),
      endpoint_url: row.target_host || "",
      response_status: typeof row.status_code === "number" ? row.status_code : null,
      response_time_ms: null,
      success: row.outcome === "delivered",
      event: row.event_type || "unknown",
      outcome: row.outcome || "failed",
    }));

  return NextResponse.json({ attempts, ledger_unavailable: !ledger.ok });
}
