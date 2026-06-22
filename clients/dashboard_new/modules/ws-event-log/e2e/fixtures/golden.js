/**
 * golden.js — the single source of truth for the events the L4 harness mounts and asserts on.
 *
 * Shared by BOTH sides of the gate so they can never drift:
 *   • the fixture page (mount.html) imports these and renders <WsEventLog events={GOLDEN_EVENTS} />
 *   • the spec (render.spec.ts) imports these and asserts the DOM rendered exactly these rows
 *
 * These rows are the `{ ts, type, summary }` shape the brick consumes — pre-summarized `ws.v1` frames
 * (modeled in @vexa/dash-contracts): a `meeting.status` and two `transcript` rows. They are listed in
 * ARRIVAL order; the component renders them NEWEST FIRST, so GOLDEN_ROWS_RENDERED below is the reversed
 * order the DOM must show.
 */

export const GOLDEN_EVENTS = [
  { ts: "10:00:00", type: "meeting.status", summary: "status: active" },
  { ts: "10:00:05", type: "transcript", summary: "Alice: the bot is in the room" },
  { ts: "10:00:09", type: "transcript", summary: "Bob: and the browser rendered this row" },
];

/** Newest first — the exact order + content the DOM rows must show. */
export const GOLDEN_ROWS_RENDERED = [...GOLDEN_EVENTS].reverse().map((e) => ({
  ts: e.ts,
  type: e.type,
  summary: e.summary,
}));
