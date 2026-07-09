import { describe, it, expect } from "vitest";
import traceJson from "../../surfaces/__tests__/fixtures/session-trace-meeting35.json";
import { processedNotesOf } from "../../surfaces/liveMeetings";
import { deriveProcessingView, defaultProcessingView } from "../processingView";

/* A REAL recorded session (meeting 35, dumped via debugTrace). At `stopping` AND `completed` the
 * durable by-id response already carries the processed notes — so "notes hidden after stop" was NEVER
 * missing data; it was the stale-`live` view gate. [N8] reload-equivalence: durable truth wins. */
const trace = traceJson as unknown as {
  events: Array<{ kind: string; meetingId: string; data: any }>;
};

const completed = trace.events
  .filter((e) => e.kind === "rest" && e.meetingId === "35" && e.data?.status === "completed")
  .map((e) => e.data)
  .pop();

describe("processed-notes-on-stop (fixture: session-trace-meeting35)", () => {
  it("durable by-id at completed carries the processed notes (data is present the whole time)", () => {
    expect(completed).toBeTruthy();
    expect(completed.status).toBe("completed");
    expect(processedNotesOf(completed).length).toBeGreaterThan(0);
  });

  it("shows Processed after stop even when the meetings-list `live` flag is STALE (durable terminal wins)", () => {
    const notes = processedNotesOf(completed);
    const hasNotes = notes.length > 0;
    const durableTerminal = ["completed", "failed", "stopped"].includes(completed.status);
    // the bug condition: the meetings-list row is stuck live after stop → live=true
    expect(deriveProcessingView({ override: null, live: true, hasNotes, durableTerminal })).toBe(true);
    // documents the OLD behaviour that produced "raw until reload":
    expect(defaultProcessingView(true, hasNotes)).toBe(false);
  });

  it("a user override still wins over the default in both directions", () => {
    expect(deriveProcessingView({ override: false, live: false, hasNotes: true, durableTerminal: true })).toBe(false);
    expect(deriveProcessingView({ override: true, live: true, hasNotes: false, durableTerminal: false })).toBe(true);
  });
});
