import { describe, expect, it } from "vitest";
import { buildProcessedNotes, extractNoteTags } from "../notes";
import { cleanProcessedNoteText, cleanTranscriptText, extractNotableNumbers } from "../textSignals";

describe("transcript text cleanup", () => {
  it("repairs common transcript artifacts before display", () => {
    const clean = cleanTranscriptText("Deep Sea closes a $7.4 b illion round. China's paying. University Chicago. Try Cloud Code and Entropic.");

    expect(clean).toContain("DeepSeek closes a $7.4 billion round.");
    expect(clean).toContain("China is paying.");
    expect(clean).toContain("University of Chicago.");
    expect(clean).toContain("Claude Code and Anthropic.");
  });

  it("removes third-person speaker boilerplate from processed notes", () => {
    expect(cleanProcessedNoteText("Speaker announces Anthropic released a new feature called Claude Tag."))
      .toBe("Anthropic released a new feature called Claude Tag.");
    expect(cleanProcessedNoteText("Speaker describes Claude Tag as a convenient way to invoke Claude from Slack."))
      .toBe("Claude Tag is a convenient way to invoke Claude from Slack.");
    expect(cleanProcessedNoteText("Speaker mentions using Slack and finding the feature appealing."))
      .toBe("I use Slack and find the feature appealing.");
    expect(cleanProcessedNoteText("Speaker believes Anthropic is entering knowledge work."))
      .toBe("Anthropic is entering knowledge work.");
    expect(cleanProcessedNoteText("Speaker expresses fear that Anthropic aims to own knowledge work."))
      .toBe("I'm afraid Anthropic aims to own knowledge work.");
  });
});

describe("notable number extraction", () => {
  it("keeps meaningful figures and ignores generic durations", () => {
    const labels = extractNotableNumbers([
      "Google loses two generational scientists in 48 hours.",
      "DeepSeek closes $7.4 billion. At a $50 billion price, the $725 billion question keeps coming up.",
      "The team needs 200 seats, p95 under 90 ms, 18%, Q3, 2026, and 12 MB chunks.",
    ]).map((item) => item.text);

    expect(labels).toEqual(expect.arrayContaining(["$7.4 billion", "$50 billion", "$725 billion", "200 seats", "90 ms", "18%", "Q3", "2026", "12 MB"]));
    expect(labels).not.toContain("48");
    expect(labels).not.toContain("48 hours");
  });
});

describe("live note tag extraction", () => {
  it("tags only explicit meeting entities and keeps numbers/noisy words plain", () => {
    const text = cleanTranscriptText("I think University of Chicago and European universities came up. Deep Sea closes $7.4 billion Series A after 48 hours. Only two of the Mag 7 outperformed the S&P.");
    const labels = extractNoteTags(text, [
      { id: "company:university-of-chicago", kind: "company", name: "University of Chicago", context: "Institution" },
      { id: "product:deepseek", kind: "product", name: "DeepSeek" },
    ], 0).map((tag) => tag.label);

    expect(labels).toContain("University of Chicago");
    expect(labels).toContain("DeepSeek");
    expect(labels).not.toContain("University");
    expect(labels).not.toContain("Chicago");
    expect(labels).not.toContain("European");
    expect(labels).not.toContain("$7.4 billion");
    expect(labels).not.toContain("Series A");
    expect(labels).not.toContain("Mag 7");
    expect(labels).not.toContain("S&P");
    expect(labels).not.toContain("48");
    expect(labels).not.toContain("S");
    expect(labels).not.toContain("P");
  });
});

describe("live note shaping", () => {
  it("renders each raw segment 1:1 while a processed note replaces the segment it covers", () => {
    const notes = buildProcessedNotes(
      [{ id: "seg-2", speaker: "Jane", text: "Speaker mentions using Slack and finding the feature appealing.", ts: 12 }],
      [
        { id: "seg-0", speaker: "Jane", text: "first raw line", ts: 1 },
        { id: "seg-1", speaker: "Jane", text: "second raw line", ts: 2 },
        { id: "seg-2", speaker: "Jane", text: "noisy second processed source", ts: 12 },
        { id: "seg-3", speaker: "Jane", text: "third raw line", ts: 13 },
        { id: "seg-4", speaker: "Jane", text: "fourth raw line", ts: 14 },
        { id: "seg-5", speaker: "Jane", text: "fifth raw line", ts: 15 },
      ],
      [],
    );

    expect(notes.map((note) => note.text)).toEqual([
      "First raw line",
      "Second raw line",
      "I use Slack and find the feature appealing.",
      "Third raw line",
      "Fourth raw line",
      "Fifth raw line",
    ]);
    expect(notes.map((note) => note.id)).toEqual(["seg-0", "seg-1", "seg-2", "seg-3", "seg-4", "seg-5"]);
  });
});
