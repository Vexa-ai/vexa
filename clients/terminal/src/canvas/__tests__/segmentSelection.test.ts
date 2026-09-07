/** WHERE A SELECTED PASSAGE WAS SAID (Vexa-ai/vexa#1596) — the transcript's `sourceRange`.
 *
 *  The claim under test is the one that makes the act's provenance trustworthy: the segment,
 *  speaker and time travel ONLY when the passage can be located exactly. Everything else — a
 *  passage said twice, a passage that spans the live edge, a transcript with no ids — comes back
 *  empty, and empty means "not established", never "none". A wrong speaker on a page's source line
 *  is worse than no speaker, because nothing downstream can tell it is wrong.
 */
import { describe, expect, it } from "vitest";
import { segmentRef } from "../segmentSelection";
import type { TranscriptSegment } from "../types";

const AT = Date.UTC(2026, 8, 6, 11, 52, 0);

const SAID: TranscriptSegment[] = [
  { id: "s1", speaker: "Jane", text: "we looked at Kaar Tech last week", tsMs: AT, completed: true },
  { id: "s2", speaker: "Ravi", text: "their pilot ships in March, self-hosted", tsMs: AT + 9000, completed: true },
  { id: "s3", speaker: "Jane", text: "and the budget sits with procurement", tsMs: AT + 21000, completed: true },
];

describe("the segment a passage was said in", () => {
  it("names the speaker and the time when the words occur exactly once", () => {
    expect(segmentRef(SAID, "their pilot ships in March")).toEqual({
      segment: "s2", speaker: "Ravi", at: new Date(AT + 9000).toISOString(),
    });
  });

  it("answers for the segment the passage STARTS in when it runs across two", () => {
    // The renderer merges consecutive same-speaker segments into one block, so a drag inside a
    // block routinely crosses a segment boundary. Where it began is the honest answer.
    expect(segmentRef(SAID, "in March, self-hosted and the budget")).toMatchObject({ segment: "s2", speaker: "Ravi" });
  });

  it("says NOTHING about a passage the room said twice", () => {
    const twice: TranscriptSegment[] = [
      { id: "s1", speaker: "Jane", text: "let us park that", tsMs: AT, completed: true },
      { id: "s2", speaker: "Ravi", text: "let us park that", tsMs: AT + 5000, completed: true },
    ];
    expect(segmentRef(twice, "let us park that")).toEqual({});
  });

  it("says nothing about words that are not in the transcript at all", () => {
    expect(segmentRef(SAID, "a sentence nobody said")).toEqual({});
  });

  it("ignores the live tail — a pending segment has no stable id to point at", () => {
    const pending: TranscriptSegment[] = [
      { id: "s1", speaker: "Jane", text: "so the next thing is", tsMs: AT, completed: true },
      { id: "p9", speaker: "Jane", text: "the migration window in", completed: false },
    ];
    expect(segmentRef(pending, "the migration window in")).toEqual({});
    expect(segmentRef(pending, "so the next thing is")).toMatchObject({ segment: "s1" });
  });

  it("matches through the whitespace of the layout — a selection is not re-typed", () => {
    // A drag across a rendered block arrives with the newlines and double spaces of the render in
    // it; the segments carry the whitespace of the ASR. Neither is a fact about the words.
    expect(segmentRef(SAID, "  their   pilot\nships in March ")).toMatchObject({ segment: "s2" });
  });

  it("gives what it has and omits what it does not — an id-less, clock-less transcript", () => {
    const bare: TranscriptSegment[] = [{ speaker: "Jane", text: "the pilot ships in March" }];
    expect(segmentRef(bare, "the pilot ships in March")).toEqual({ speaker: "Jane" });
  });

  it("returns an empty answer rather than throwing on nothing at all", () => {
    expect(segmentRef([], "anything")).toEqual({});
    expect(segmentRef(undefined, "anything")).toEqual({});
    expect(segmentRef(SAID, "   ")).toEqual({});
  });
});
