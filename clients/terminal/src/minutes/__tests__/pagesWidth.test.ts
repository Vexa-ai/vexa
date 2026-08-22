import { describe, expect, it } from "vitest";
import { clampPagesWidth, maxPagesWidth } from "../pagesWidth";
import { T } from "../tokens";

describe("minutes pages panel width", () => {
  it("keeps the normal document width narrow", () => {
    expect(T.pagesDefault).toBe(384);
  });

  it("uses all available room instead of stopping at the former 720px ceiling", () => {
    expect(maxPagesWidth(1920)).toBe(1920 - T.railW - T.conversationMin);
    expect(clampPagesWidth(1200, 1920)).toBe(1200);
  });

  it("preserves the conversation floor when the document panel is dragged wider", () => {
    expect(clampPagesWidth(1600, 1440)).toBe(1440 - T.railW - T.conversationMin);
  });

  it("preserves the document floor in a constrained window", () => {
    expect(clampPagesWidth(100, 800)).toBe(T.pagesMin);
  });
});
