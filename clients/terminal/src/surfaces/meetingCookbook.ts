/** meetingCookbook — top-level COOKBOOK operations that compose ≥2 domain contracts into one
 *  state-delivering call, ABOVE both domains (`meetings ⊥ agent`). Composition lives HERE, never inside a
 *  domain — see docs/CONTROL-PLANE.md §5. Reusable by the terminal and any client.
 *
 *  Cookbook #2 — "agent on a meeting": send the bot (MEETINGS: POST /bots) AND enable the copilot
 *  (AGENT: POST /api/meeting/process), returning the combined state. `send bot ≠ start copilot` — two
 *  toggles, two domains — composed into one op. Partial failure is SURFACED in the returned state, never
 *  swallowed (P18). */
import { ApiError, getJson } from "./apiClient";
import { defaultBotName } from "./defaultBotName";

export interface AgentOnMeetingInput {
  platform: string; // e.g. "google_meet"
  native_id: string; // the meeting's native id
  meeting_url?: string; // optional — derived for google_meet when absent
  bot_name?: string;
}

export interface AgentOnMeetingState {
  platform: string;
  native_id: string;
  bot: { sent: boolean; status?: string }; // meetings domain
  copilot: { enabled: boolean; resumed_from?: string; error?: string }; // agent domain
}

function meetingUrlFor(platform: string, native: string, given?: string): string | undefined {
  if (given) return given;
  return platform === "google_meet" ? `https://meet.google.com/${native}` : undefined;
}

/** Deliver the STATE "agent listening on this meeting" by composing the two published domain contracts.
 *  Step 1 (meetings) sends the bot — a HARD failure here throws (no bot ⇒ no transcript ⇒ nothing to do).
 *  Step 2 (agent) enables the copilot — a failure is SURFACED in `copilot.error` (the bot is already in
 *  the meeting and the raw transcript still flows), never swallowed. */
export async function agentOnMeeting(input: AgentOnMeetingInput): Promise<AgentOnMeetingState> {
  const { platform, native_id } = input;
  // Step 1 — MEETINGS domain: send the bot (POST /bots through the gateway edge). The response carries
  // the meetings-domain ROW id (`id`) — passed to /api/meeting/process below so the copilot's opt-in
  // flag / cursor / processed stream key on the row id (P0 cross-tenant leak fix), never the native.
  const bot = await getJson<{ status?: string; id?: number | string }>("/api/bots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      platform,
      native_meeting_id: native_id,
      meeting_url: meetingUrlFor(platform, native_id, input.meeting_url),
      bot_name: input.bot_name ?? defaultBotName(),
    }),
  });
  // Step 2 — AGENT domain: enable the copilot (POST /api/meeting/process). Surface a failure, don't throw.
  let copilot: AgentOnMeetingState["copilot"] = { enabled: false };
  try {
    const proc = await getJson<{ resumed_from?: string }>("/api/meeting/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ native_id, platform, on: true, meeting_id: bot.id != null ? String(bot.id) : undefined }),
    });
    copilot = { enabled: true, resumed_from: proc.resumed_from };
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    copilot = { enabled: false, error: e.detail || `process → ${e.status}` };
  }
  return { platform, native_id, bot: { sent: true, status: bot.status }, copilot };
}
