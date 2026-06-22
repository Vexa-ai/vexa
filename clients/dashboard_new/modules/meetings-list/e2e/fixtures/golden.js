/**
 * golden.js — the single source of truth for the meetings the L4 harness mounts and asserts on.
 *
 * Shared by BOTH sides of the gate so they can never drift:
 *   • the fixture page (list-render.html) imports these, mounts MeetingsList over them, and records
 *     onOpen calls onto window
 *   • the spec (list-render.spec.ts) imports these and asserts the DOM rendered exactly this
 *
 * Two real api.v1 MeetingResponse items (the shape @vexa/dash-contracts models, GET /meetings items):
 *   - GOLDEN_ACTIVE:    a google_meet meeting still "active" (no end_time → duration "—")
 *   - GOLDEN_COMPLETED: a teams meeting "completed" with a start+end span (15m), and a data.name title
 */

export const GOLDEN_ACTIVE = {
  id: 101,
  user_id: 7,
  platform: "google_meet",
  native_meeting_id: "abc-defg-hij",
  constructed_meeting_url: "https://meet.google.com/abc-defg-hij",
  status: "active",
  bot_container_id: "bot-active-101",
  start_time: "2026-06-22T10:00:00Z",
  end_time: null,
  data: { participants: ["Alice", "Bob"] },
  created_at: "2026-06-22T09:59:00Z",
  updated_at: "2026-06-22T10:00:00Z",
};

export const GOLDEN_COMPLETED = {
  id: 102,
  user_id: 7,
  platform: "teams",
  native_meeting_id: "19:meeting_ZmFrZQ@thread.v2",
  constructed_meeting_url: "https://teams.microsoft.com/l/meetup-join/...",
  status: "completed",
  bot_container_id: "bot-completed-102",
  start_time: "2026-06-22T08:00:00Z",
  end_time: "2026-06-22T08:15:00Z",
  data: { name: "Weekly Sync" },
  created_at: "2026-06-22T07:59:00Z",
  updated_at: "2026-06-22T08:15:00Z",
};

/** The two golden meetings, in render order. */
export const GOLDEN_MEETINGS = [GOLDEN_ACTIVE, GOLDEN_COMPLETED];

/** Expected per-row facts the spec asserts (index-aligned with GOLDEN_MEETINGS). */
export const EXPECTED_ROWS = [
  { id: "101", status: "active", statusLabel: "Active", duration: "—", nativeId: "abc-defg-hij" },
  { id: "102", status: "completed", statusLabel: "Completed", duration: "15m", nativeId: "19:meeting_ZmFrZQ@thread.v2" },
];
