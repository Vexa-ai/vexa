import { describe, expect, it } from "vitest";
import { buildMeetingNotes } from "../notes";
import type { TranscriptSegment } from "../types";

/**
 * Contract-fidelity regression tests for buildMeetingNotes.
 *
 * Signature:
 *   buildMeetingNotes(
 *     processedInput: ProcessedTranscriptNote[] | undefined,
 *     segmentInput: TranscriptSegment[] | undefined,
 *     entities: EntityItem[],
 *   ): MeetingNote[]
 *
 * The original bug: a trailing PENDING (completed:false) segment was dropped because
 * its group was shorter than CLUSTER (3) and so never flushed. The live in-flight line
 * never reached the view, and the `completed:false` marker was lost.
 */
describe("buildMeetingNotes — pending tail fidelity", () => {
  it("emits the trailing pending (completed:false) segment as its own live note", () => {
    const segments: TranscriptSegment[] = [
      { id: "s0", speaker: "Jane", text: "first finalized line", ts: 1, completed: true },
      { id: "s1", speaker: "Jane", text: "second finalized line", ts: 2, completed: true },
      { id: "s2", speaker: "Jane", text: "third finalized line", ts: 3, completed: true },
      // trailing in-progress ASR line — must survive even though its group is < CLUSTER.
      { id: "s3", speaker: "Jane", text: "pending in flight line", ts: 4, completed: false },
    ];

    const notes = buildMeetingNotes(undefined, segments, []);

    const pending = notes.find((n) => n.text.toLowerCase().includes("pending in flight"));
    expect(pending, "pending tail note must not be dropped").toBeDefined();
    expect(pending?.completed).toBe(false);
  });

  it("preserves a lone trailing pending segment (group size 1, < CLUSTER)", () => {
    const segments: TranscriptSegment[] = [
      { id: "s0", speaker: "Ann", text: "only one pending line", ts: 1, completed: false },
    ];

    const notes = buildMeetingNotes(undefined, segments, []);

    expect(notes).toHaveLength(1);
    expect(notes[0].completed).toBe(false);
    expect(notes[0].text.toLowerCase()).toContain("only one pending line");
  });

  it("still merges/flushes a finalized run and marks it finalized (completed undefined)", () => {
    const segments: TranscriptSegment[] = [
      { id: "s0", speaker: "Bob", text: "alpha", ts: 1, completed: true },
      { id: "s1", speaker: "Bob", text: "beta", ts: 2, completed: true },
      { id: "s2", speaker: "Bob", text: "gamma", ts: 3, completed: true },
    ];

    const notes = buildMeetingNotes(undefined, segments, []);

    // A full CLUSTER of same-speaker finalized lines merges into one note.
    expect(notes).toHaveLength(1);
    expect(notes[0].text).toBe("Alpha beta gamma");
    // Finalized notes carry completed === undefined (not false).
    expect(notes[0].completed).toBeUndefined();
  });

  it("keeps a finalized run AND a following pending tail as distinct notes", () => {
    const segments: TranscriptSegment[] = [
      { id: "s0", speaker: "Cara", text: "alpha", ts: 1, completed: true },
      { id: "s1", speaker: "Cara", text: "beta", ts: 2, completed: true },
      { id: "s2", speaker: "Cara", text: "gamma", ts: 3, completed: true },
      { id: "s3", speaker: "Cara", text: "live tail", ts: 4, completed: false },
    ];

    const notes = buildMeetingNotes(undefined, segments, []);

    const completedFlags = notes.map((n) => n.completed);
    // exactly one finalized (undefined) note and one pending (false) note
    expect(completedFlags).toContain(undefined);
    expect(completedFlags).toContain(false);
    const tail = notes[notes.length - 1];
    expect(tail.completed).toBe(false);
    expect(tail.text.toLowerCase()).toContain("live tail");
  });
});
