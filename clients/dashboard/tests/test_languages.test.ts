/**
 * Unit tests for language utilities — codes, display names, sorting.
 */
import { describe, it, expect } from "vitest";
import {
  WHISPER_LANGUAGE_NAMES,
  WHISPER_LANGUAGE_CODES,
  getLanguageDisplayName,
} from "@/lib/languages";

describe("WHISPER_LANGUAGE_NAMES", () => {
  it("contains English", () => {
    expect(WHISPER_LANGUAGE_NAMES["en"]).toBe("English");
  });

  it("has more than 90 languages", () => {
    expect(Object.keys(WHISPER_LANGUAGE_NAMES).length).toBeGreaterThan(90);
  });
});

describe("WHISPER_LANGUAGE_CODES", () => {
  it("English is the first code (most popular)", () => {
    expect(WHISPER_LANGUAGE_CODES[0]).toBe("en");
  });

  it("has same count as WHISPER_LANGUAGE_NAMES", () => {
    expect(WHISPER_LANGUAGE_CODES.length).toBe(
      Object.keys(WHISPER_LANGUAGE_NAMES).length
    );
  });

  it("contains no duplicates", () => {
    const unique = new Set(WHISPER_LANGUAGE_CODES);
    expect(unique.size).toBe(WHISPER_LANGUAGE_CODES.length);
  });
});

describe("getLanguageDisplayName", () => {
  it("returns Auto-detect for 'auto'", () => {
    expect(getLanguageDisplayName("auto")).toBe("Auto-detect");
  });

  it("returns English for 'en'", () => {
    expect(getLanguageDisplayName("en")).toBe("English");
  });

  it("returns uppercased code for unknown codes", () => {
    expect(getLanguageDisplayName("xx")).toBe("XX");
  });
});
