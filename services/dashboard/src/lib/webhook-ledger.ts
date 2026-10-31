/**
 * The core's per-user webhook delivery ledger (#841), read with the signed-in user's own key.
 *
 * `GET {VEXA_API_URL}/user/webhook/deliveries` is owner-scoped by the gateway, so the dashboard
 * never passes a user id. Each row carries the target host only, never the URL or the secret (P14).
 */

export type LedgerOutcome = "delivered" | "queued" | "suppressed" | "blocked" | "failed";

export interface LedgerRow {
  event_type?: string | null;
  event_id?: string | null;
  target_host?: string | null;
  outcome?: string | null;
  status_code?: number | null;
  attempt?: number | null;
  created_at?: string | null;
  meeting_id?: number | string | null;
}

export type LedgerFetch =
  | { ok: true; rows: LedgerRow[] }
  | { ok: false; rows: [] };

export async function fetchLedgerRows(
  apiUrl: string,
  token: string,
  limit = 100
): Promise<LedgerFetch> {
  try {
    const res = await fetch(`${apiUrl}/user/webhook/deliveries?limit=${limit}`, {
      headers: { "X-API-Key": token },
      cache: "no-store",
    });
    if (!res.ok) return { ok: false, rows: [] };
    const body = await res.json();
    const rows = Array.isArray(body?.deliveries) ? (body.deliveries as LedgerRow[]) : [];
    return { ok: true, rows };
  } catch {
    return { ok: false, rows: [] };
  }
}

/** The filter/stats bucket for a named outcome. */
export function statusForOutcome(outcome: string | null | undefined) {
  switch (outcome) {
    case "delivered":
      return "delivered" as const;
    case "queued":
      return "retrying" as const;
    case "suppressed":
      return "suppressed" as const;
    default:
      return "failed" as const; // failed, blocked, unknown
  }
}

/** One ledger row → the UI's WebhookDelivery shape. */
export function mapLedgerRow(row: LedgerRow, index: number) {
  const meetingId = row.meeting_id === null || row.meeting_id === undefined ? "" : String(row.meeting_id);
  const createdAt = row.created_at || new Date(0).toISOString();
  const attempts = (typeof row.attempt === "number" ? row.attempt : 0) + 1;
  return {
    id: row.event_id || `ledger-${index}`,
    event: row.event_type || "unknown",
    meeting_id: meetingId,
    meeting_name: meetingId ? `Meeting ${meetingId}` : "—",
    outcome: row.outcome || "failed",
    status: statusForOutcome(row.outcome),
    attempts,
    max_attempts: attempts,
    response_status: typeof row.status_code === "number" ? row.status_code : null,
    response_time_ms: null,
    endpoint_url: row.target_host || "",
    created_at: createdAt,
    last_attempt_at: createdAt,
    source: "ledger" as const,
  };
}
