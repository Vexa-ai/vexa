/** Founder 2026-10-16: the home composer flickered in and out of sticky while scrolling. Going
 *  sticky must not move the layout: the space the folded composer gives back is held by a spacer. */
import { describe, expect, it } from "vitest";
import { stickySpacer } from "../desktop/home2/HomeTarget";

describe("stickySpacer", () => {
  it("holds the unstuck height while stuck, and nothing while unstuck", () => {
    expect(stickySpacer(true, 140, 60)).toBe(80);
    expect(stickySpacer(false, 140, 140)).toBe(0);
    expect(stickySpacer(true, 60, 80)).toBe(0);
  });
});
