import { describe, expect, it } from "vitest";
import { shouldShowRoomCount } from "./alloyRoomCount";

describe("ALLOY room count visibility", () => {
  it("preserves upstream rendering when the flag is disabled", () => {
    expect(shouldShowRoomCount(0, "0")).toBe(true);
  });

  it("hides only the placeholder zero when enabled", () => {
    expect(shouldShowRoomCount(0, "1")).toBe(false);
    expect(shouldShowRoomCount(2, "1")).toBe(true);
  });
});
