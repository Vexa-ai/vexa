/**
 * golden.js — the single source of truth for the props the L4 harness mounts and asserts on.
 *
 * Shared by BOTH sides of the gate so they can never drift:
 *   • the fixture page (render.html) imports GOLDEN_SEGMENTS and mounts <TranscriptViewer/> with them
 *   • the spec (render.spec.ts) imports GOLDEN_SPEAKERS / GOLDEN_TEXTS and asserts the rendered DOM
 *
 * Two confirmed segments from two speakers ("Anna", "Zoya"), shaped per @vexa/dash-contracts
 * `TranscriptSegment`. isLive is on so the live indicator is also exercised.
 */
export const GOLDEN_SEGMENTS = [
  {
    text: "Anna kicks off the standup",
    speaker: "Anna",
    start_time: 0,
    end_time: 3,
    absolute_start_time: "2026-06-22T10:00:00Z",
    absolute_end_time: "2026-06-22T10:00:03Z",
    completed: true,
    segment_id: "seg-1",
  },
  {
    text: "Zoya reports the deploy is green",
    speaker: "Zoya",
    start_time: 4,
    end_time: 8,
    absolute_start_time: "2026-06-22T10:00:04Z",
    absolute_end_time: "2026-06-22T10:00:08Z",
    completed: true,
    segment_id: "seg-2",
  },
];

/** The two speaker names the rendered DOM must show. */
export const GOLDEN_SPEAKERS = GOLDEN_SEGMENTS.map((s) => s.speaker);

/** The two segment texts the rendered DOM must show. */
export const GOLDEN_TEXTS = GOLDEN_SEGMENTS.map((s) => s.text);
