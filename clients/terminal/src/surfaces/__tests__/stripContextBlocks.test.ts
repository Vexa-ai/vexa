/** History hygiene: server-side grounding preambles never render as the user's words. */
import { describe, expect, it } from "vitest";
import { stripContextBlocks } from "../chat";

describe("stripContextBlocks", () => {
  it("strips stacked kg-links + mount-stack preambles down to the user's text", () => {
    const raw = "## Referencing knowledge (always)\n\nblah rules — create the entity first, or use plain text.\n\n" +
      "## Your mounted workspaces\n\ntier list...\nAlways use ABSOLUTE paths under the mount you intend — do not guess or invent mount paths.\nwhat did we decide in my last meeting?";
    expect(stripContextBlocks(raw)).toBe("what did we decide in my last meeting?");
  });
  it("strips a live-meeting transcript fold", () => {
    const raw = "You are assisting in a live meeting (google_meet/abc). Its live transcript so far is below — answer.\n\n<transcript>\nJane: hi\n</transcript>\n\nwho spoke?";
    expect(stripContextBlocks(raw)).toBe("who spoke?");
  });
  it("leaves unrecognized text untouched (fail-soft)", () => {
    expect(stripContextBlocks("plain question")).toBe("plain question");
    const odd = "## Your mounted workspaces\nnever-terminated";
    expect(stripContextBlocks(odd)).toBe(odd);
  });
});
