import type {
  MeetingStatus,
  WebSocketIncomingMessage,
} from "@/types/vexa";

/**
 * Resolve a meeting status from any ws.v1 status frame.
 *
 * Two wire shapes carry status over /ws:
 *  - `bot_status` — the sealed ws.v1 contract frame (#/$defs/BotStatus, see
 *    core/gateway/contracts/ws.v1/ws.schema.json and the BotStatus.recording
 *    golden). The gateway forwards the raw redis `bm:meeting:{id}:status`
 *    payload verbatim, so `status` (and optional `meeting_id`) live at the top
 *    level: `{ "type": "bot_status", "status": "...", "meeting_id": 42 }`.
 *  - `meeting.status` — the legacy frame that nests status under `payload`.
 *    Kept for back-compat.
 *
 * Returns the status string, or null if the message is not a status frame /
 * carries no status.
 */
/**
 * The core's ws.v1 BotStatus enum spells the help state `needs_help`; the dashboard's
 * MeetingStatus union spells it `needs_human_help`. That legacy naming lives HERE, on the
 * dashboard side — the dashboard adapts the contract, the contract never carries the dashboard's
 * quirk. (The legacy `meeting.status` frame already carries dashboard values, so the map is a
 * no-op for it.) Extend this map if the dashboard's vocabulary diverges from the core enum again.
 */
const CONTRACT_TO_DASHBOARD_STATUS: Record<string, MeetingStatus> = {
  needs_help: "needs_human_help",
};

function normalizeStatus(raw: string | null | undefined): MeetingStatus | null {
  if (!raw) return null;
  return (CONTRACT_TO_DASHBOARD_STATUS[raw] ?? raw) as MeetingStatus;
}

export function resolveStatusFromMessage(
  message: WebSocketIncomingMessage
): MeetingStatus | null {
  if (message.type === "bot_status") {
    return normalizeStatus(message.status);
  }
  if (message.type === "meeting.status") {
    return normalizeStatus(message.payload?.status);
  }
  return null;
}
