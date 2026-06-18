/**
 * Unit tests for dashboard utility functions.
 *
 * Tests pure logic in src/lib/utils.ts — cn() class merging and
 * parseUTCTimestamp() timezone handling.
 */
import { describe, it, expect } from "vitest";
import { cn, parseUTCTimestamp } from "@/lib/utils";

describe("cn", () => {
  it("merges class names", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    expect(cn("base", false && "hidden", "visible")).toBe("base visible");
  });

  it("deduplicates tailwind classes", () => {
    // twMerge should pick the last conflicting utility
    expect(cn("p-4", "p-2")).toBe("p-2");
  });

  it("handles empty input", () => {
    expect(cn()).toBe("");
  });
});

describe("parseUTCTimestamp", () => {
  it("appends Z to bare timestamps", () => {
    const d = parseUTCTimestamp("2025-12-11T14:20:25.222296");
    expect(d.getUTCHours()).toBe(14);
    expect(d.getUTCMinutes()).toBe(20);
  });

  it("does not modify timestamps with Z suffix", () => {
    const d = parseUTCTimestamp("2025-12-11T14:20:25Z");
    expect(d.getUTCHours()).toBe(14);
  });

  it("does not modify timestamps with timezone offset", () => {
    const d = parseUTCTimestamp("2025-12-11T14:20:25+03:00");
    expect(d.getUTCHours()).toBe(11);
  });

  it("handles lowercase z", () => {
    const d = parseUTCTimestamp("2025-12-11T14:20:25z");
    expect(d.getUTCHours()).toBe(14);
  });
});
