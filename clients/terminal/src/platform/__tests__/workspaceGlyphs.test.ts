import { describe, expect, it } from "vitest";
import { GENERIC_GLYPHS, GLYPHS } from "../workspaceGlyphs.data";
import { genericGlyph, matchGlyph, searchGlyphs, stem } from "../workspaceGlyphs";

// The same cases as core/agent/tests/test_workspace_glyph.py: the two matchers must agree.
export const CASES: [string, string, string | null][] = [
  ["Product planning", "", "map"],
  ["Company handbook", "", "book-open"],
  ["Garden club", "", "sprout"],
  ["Growth", "", "chart-line"],
  ["OeNB pilot", "", "landmark"],
  ["Northbank pilot", "Pilot with the central bank", "landmark"],
  ["Hiring", "", "user-plus"],
  ["Book club", "", "book-marked"],
  ["Weekly sync", "", "calendar-clock"],
  ["Gardening", "", "sprout"],
  ["Café crew", "", "coffee"],
  ["Untitled", "", null],
];

describe("workspace glyphs", () => {
  it("every glyph has strokes and keywords, and the generic set is in the list", () => {
    expect(GLYPHS.length).toBeGreaterThan(200);
    for (const g of GLYPHS) { expect(g.nodes.length).toBeGreaterThan(0); expect(g.keywords.length).toBeGreaterThan(0); }
    for (const n of GENERIC_GLYPHS) expect(GLYPHS.some((g) => g.name === n)).toBe(true);
  });
  it.each(CASES)("%s (%s) → %s", (name, purpose, want) => {
    expect(matchGlyph(GLYPHS, name, purpose)).toBe(want);
  });
  it("stems plurals and -ing", () => {
    expect(stem("Gardening")).toBe("garden");
    expect(stem("libraries")).toBe("library");
    expect(stem("boxes")).toBe("box");
  });
  it("a generic pick is stable per id", () => {
    expect(genericGlyph(GENERIC_GLYPHS, "untitled-1a2b")).toBe(genericGlyph(GENERIC_GLYPHS, "untitled-1a2b"));
    expect(GENERIC_GLYPHS).toContain(genericGlyph(GENERIC_GLYPHS, "x"));
  });
  it("search matches names and keywords by prefix", () => {
    expect(searchGlyphs(GLYPHS, "bank").map((g) => g.name)).toContain("landmark");
    expect(searchGlyphs(GLYPHS, "plan").map((g) => g.name)).toContain("sprout");
    expect(searchGlyphs(GLYPHS, "").length).toBe(GLYPHS.length);
  });
});
