import { describe, expect, it } from "vitest";
import { MAX_EMAIL_LENGTH, isValidEmailFormat } from "@/lib/email-format";

// The pattern the parser replaces; kept here only as the behavioural oracle.
const PREVIOUS_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

describe("isValidEmailFormat", () => {
  const accepted = [
    "user@example.com",
    "first.last+tag@sub.example.co.uk",
    "a@b.c",
    "x@a.b.",
    "x@-.-",
    "user@example..com",
  ];
  const refused = [
    "",
    "plain",
    "@example.com",
    "user@",
    "user@example",
    "user@.com",
    "user@com.",
    "user@@example.com",
    "us@er@example.com",
    "user @example.com",
    "user@exa mple.com",
    "user@example.com\n",
    "\tuser@example.com",
  ];

  it.each(accepted)("accepts %j, as the previous pattern did", (value) => {
    expect(PREVIOUS_PATTERN.test(value)).toBe(true);
    expect(isValidEmailFormat(value)).toBe(true);
  });

  it.each(refused)("refuses %j, as the previous pattern did", (value) => {
    expect(PREVIOUS_PATTERN.test(value)).toBe(false);
    expect(isValidEmailFormat(value)).toBe(false);
  });

  it("matches the previous pattern on generated inputs up to the length cap", () => {
    const alphabet = ["a", "@", ".", " ", "b"];
    let seed = 7;
    const next = () => {
      seed = (seed * 1103515245 + 12345) % 2 ** 31;
      return seed;
    };
    for (let i = 0; i < 5000; i++) {
      const length = 1 + (next() % 12);
      let value = "";
      for (let j = 0; j < length; j++) value += alphabet[next() % alphabet.length];
      expect(isValidEmailFormat(value)).toBe(PREVIOUS_PATTERN.test(value));
    }
  });

  it("refuses non-strings and values over the RFC 5321 length limit", () => {
    expect(isValidEmailFormat(undefined)).toBe(false);
    expect(isValidEmailFormat(42)).toBe(false);
    const local = "a".repeat(MAX_EMAIL_LENGTH - "@example.com".length);
    expect(isValidEmailFormat(`${local}@example.com`)).toBe(true);
    expect(isValidEmailFormat(`a${local}@example.com`)).toBe(false);
  });

  it("stays fast on long adversarial input", () => {
    const value = "!@!." + "!.".repeat(100_000);
    const started = performance.now();
    expect(isValidEmailFormat(value)).toBe(false);
    expect(performance.now() - started).toBeLessThan(50);
  });
});
