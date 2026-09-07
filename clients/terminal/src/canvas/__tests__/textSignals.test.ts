import { describe, expect, it } from "vitest";
import { cleanTranscriptText, extractNotableNumbers } from "../textSignals";

describe("transcript text cleanup", () => {
  it("repairs common transcript artifacts before display", () => {
    const clean = cleanTranscriptText("Deep Sea closes a $7.4 b illion round. China's paying. University Chicago. Try Cloud Code and Entropic.");

    expect(clean).toContain("DeepSeek closes a $7.4 billion round.");
    expect(clean).toContain("China is paying.");
    expect(clean).toContain("University of Chicago.");
    expect(clean).toContain("Claude Code and Anthropic.");
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
