/**
 * golden.js — the single source of truth for the frames the harness injects and asserts on.
 *
 * Shared by BOTH sides of the L4 gate so they can never drift:
 *   • the fixture page (ws-render.html) imports these and emits them through the FakeWsTransport
 *   • the spec (ws-render.spec.ts) imports these and asserts the DOM rendered exactly this text
 *
 * These are real ws.v1 frames (the 0.10.6 truth modeled in @vexa/dash-contracts):
 *   - GOLDEN_STATUS:    a `meeting.status` with payload.status "active"
 *   - GOLDEN_TRANSCRIPT: a `transcript` bundle, two confirmed segments from two speakers
 *
 * GOLDEN_LINES is the exact per-line text the page renders into #transcript (one <div> per confirmed
 * segment, formatted `Speaker: text`). When this harness graduates to the real stack, these same
 * goldens get injected via redis instead of the fake — the DOM assertions stay identical.
 */

export const GOLDEN_MEETING = { platform: "google_meet", native_id: "abc-xyz-123" };

export const GOLDEN_STATUS = {
  type: "meeting.status",
  meeting: { id: 42, platform: "google_meet", native_id: "abc-xyz-123" },
  payload: { status: "active" },
};

export const GOLDEN_TRANSCRIPT = {
  type: "transcript",
  speaker: "Alice",
  confirmed: [
    {
      text: "the bot is in the room",
      speaker: "Alice",
      absolute_start_time: "2026-06-22T10:00:00Z",
    },
    {
      text: "and the browser rendered this line",
      speaker: "Bob",
      absolute_start_time: "2026-06-22T10:00:05Z",
    },
  ],
  pending: [],
};

/** The exact lines the page paints into #transcript, in order. `${speaker}: ${text}`. */
export const GOLDEN_LINES = GOLDEN_TRANSCRIPT.confirmed.map(
  (s) => `${s.speaker}: ${s.text}`,
);

/** The exact text #status must show. */
export const GOLDEN_STATUS_TEXT = GOLDEN_STATUS.payload.status; // "active"
