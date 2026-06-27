import { describe, expect, it } from "vitest";
import { buildProcessedNotes } from "../notes";
import type { TranscriptSegment } from "../types";

/**
 * Contract-fidelity regression tests for buildProcessedNotes (strictly 1:1 — no clustering).
 *
 * Signature:
 *   buildProcessedNotes(
 *     processedInput: ProcessedTranscriptNote[] | undefined,
 *     segmentInput: TranscriptSegment[] | undefined,
 *     entities: EntityItem[],
 *   ): MeetingNote[]
 *
 * P23: a reader never re-derives a producer's data. Each uncovered raw segment renders as exactly
 * ONE note; a trailing PENDING (completed:false) segment still surfaces as its own live line.
 */
describe("buildProcessedNotes — 1:1 fidelity", () => {
  it("renders each raw segment as its own note, preserving the pending tail", () => {
    const segments: TranscriptSegment[] = [
      { id: "s0", speaker: "Jane", text: "first finalized line", ts: 1, completed: true },
      { id: "s1", speaker: "Jane", text: "second finalized line", ts: 2, completed: true },
      { id: "s2", speaker: "Jane", text: "third finalized line", ts: 3, completed: true },
      // trailing in-progress ASR line — must survive as its own note.
      { id: "s3", speaker: "Jane", text: "pending in flight line", ts: 4, completed: false },
    ];

    const notes = buildProcessedNotes(undefined, segments, []);

    expect(notes).toHaveLength(4);
    const pending = notes.find((n) => n.text.toLowerCase().includes("pending in flight"));
    expect(pending, "pending tail note must not be dropped").toBeDefined();
    expect(pending?.completed).toBe(false);
  });

  it("preserves a lone trailing pending segment", () => {
    const segments: TranscriptSegment[] = [
      { id: "s0", speaker: "Ann", text: "only one pending line", ts: 1, completed: false },
    ];

    const notes = buildProcessedNotes(undefined, segments, []);

    expect(notes).toHaveLength(1);
    expect(notes[0].completed).toBe(false);
    expect(notes[0].text.toLowerCase()).toContain("only one pending line");
  });

  it("renders a finalized run as one note per segment, each marked finalized (completed undefined)", () => {
    const segments: TranscriptSegment[] = [
      { id: "s0", speaker: "Bob", text: "alpha", ts: 1, completed: true },
      { id: "s1", speaker: "Bob", text: "beta", ts: 2, completed: true },
      { id: "s2", speaker: "Bob", text: "gamma", ts: 3, completed: true },
    ];

    const notes = buildProcessedNotes(undefined, segments, []);

    // 1:1 — three segments, three notes, no clustering.
    expect(notes).toHaveLength(3);
    expect(notes.map((n) => n.text)).toEqual(["Alpha", "Beta", "Gamma"]);
    // Finalized notes carry completed === undefined (not false).
    expect(notes.every((n) => n.completed === undefined)).toBe(true);
  });

  it("keeps a finalized run AND a following pending tail as distinct notes", () => {
    const segments: TranscriptSegment[] = [
      { id: "s0", speaker: "Cara", text: "alpha", ts: 1, completed: true },
      { id: "s1", speaker: "Cara", text: "beta", ts: 2, completed: true },
      { id: "s2", speaker: "Cara", text: "gamma", ts: 3, completed: true },
      { id: "s3", speaker: "Cara", text: "live tail", ts: 4, completed: false },
    ];

    const notes = buildProcessedNotes(undefined, segments, []);

    expect(notes).toHaveLength(4);
    const completedFlags = notes.map((n) => n.completed);
    expect(completedFlags).toContain(undefined);
    expect(completedFlags).toContain(false);
    const tail = notes[notes.length - 1];
    expect(tail.completed).toBe(false);
    expect(tail.text.toLowerCase()).toContain("live tail");
  });
});
