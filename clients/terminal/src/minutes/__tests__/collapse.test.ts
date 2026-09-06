/** Both side columns fold away, and the choice is remembered per side.
 *
 *  Only the persistence is testable at a function boundary — the rest is a grid column that becomes
 *  22px wide — and the persistence is the part with a wrong answer worth guarding: a reader who
 *  hides the pages panel must not find the chat list gone too, and a profile that has never chosen
 *  must open with all three columns rather than with two sides missing. */
import { beforeEach, describe, expect, it } from "vitest";
import { COLLAPSED_KEY, loadCollapsed, saveCollapsed } from "../chats";

describe("side-column collapse — persisted per side", () => {
  beforeEach(() => localStorage.clear());

  it("a profile that has never chosen opens with both columns", () => {
    expect(loadCollapsed("left")).toBe(false);
    expect(loadCollapsed("right")).toBe(false);
  });

  it("a side round-trips", () => {
    saveCollapsed("left", true);
    expect(loadCollapsed("left")).toBe(true);
    saveCollapsed("left", false);
    expect(loadCollapsed("left")).toBe(false);
  });

  it("the two sides are independent — hiding one never hides the other", () => {
    saveCollapsed("right", true);
    expect(loadCollapsed("right")).toBe(true);
    expect(loadCollapsed("left")).toBe(false);
  });

  it("each side owns its own key", () => {
    saveCollapsed("left", true);
    saveCollapsed("right", true);
    expect(localStorage.getItem(COLLAPSED_KEY.left)).toBe("1");
    expect(localStorage.getItem(COLLAPSED_KEY.right)).toBe("1");
    expect(COLLAPSED_KEY.left).not.toBe(COLLAPSED_KEY.right);
  });

  it("anything but an explicit \"1\" reads as OPEN — a junk value never hides a column", () => {
    localStorage.setItem(COLLAPSED_KEY.left, "true");
    expect(loadCollapsed("left")).toBe(false);
  });
});
