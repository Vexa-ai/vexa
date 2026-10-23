import { describe, it, expect } from "vitest";
import { isQuietCheck } from "../chat";

describe("a routine's quiet wake", () => {
  it("is one line starting Checked —", () => {
    expect(isQuietCheck("Checked — nothing new.")).toBe(true);
    expect(isQuietCheck("Checked - no new alerts since 21:30")).toBe(true);
    expect(isQuietCheck("Checked — nothing new.\nBut also this")).toBe(false);
    expect(isQuietCheck("Two new replies need you: …")).toBe(false);
  });
});
