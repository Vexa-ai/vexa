/**
 * golden.js — the single source of truth for the props the harness mounts and asserts on.
 *
 * Shared by BOTH sides of the L4 gate so they can never drift:
 *   • the fixture page (render.html) imports these and mounts <StatusHistory transitions=…/>
 *   • the spec (status-history.spec.ts) imports these and asserts the DOM rendered exactly this
 *
 * GOLDEN_TRANSITIONS is the requested L4 timeline: joining → active → completed (with timestamps).
 * It's intentionally provided OUT OF ORDER (active first) so the test also proves the component sorts
 * oldest → newest by timestamp before rendering.
 */

export const GOLDEN_TRANSITIONS = [
  { from: "joining", to: "active", timestamp: "2026-06-22T10:00:05Z", source: "bot_callback" },
  { from: "requested", to: "joining", timestamp: "2026-06-22T10:00:00Z", source: "user" },
  {
    from: "active",
    to: "completed",
    timestamp: "2026-06-22T10:30:00Z",
    completion_reason: "meeting ended",
  },
];

/** The exact destination statuses, in the order the component must render them (oldest → newest). */
export const GOLDEN_STATUS_LABELS = ["Joining", "Active", "Completed"];

/** The raw `to` values in render order — asserted against each row's data-status attribute. */
export const GOLDEN_TO_ORDER = ["joining", "active", "completed"];
