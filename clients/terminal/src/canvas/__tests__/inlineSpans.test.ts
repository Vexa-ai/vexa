import { describe, expect, it } from "vitest";
import { splitTextIntoSpans, type SpanEntity } from "../inlineSpans";

const E = (label: string, kind = "person", extra: Partial<SpanEntity> = {}): SpanEntity => ({ label, kind, ...extra });

describe("splitTextIntoSpans", () => {
  it("wraps a found entity label in a span carrying its kind + entity", () => {
    const spans = splitTextIntoSpans("I spoke with Acme today", [E("Acme", "company", { id: "c1", docPath: "kg/acme.md" })]);
    expect(spans).toEqual([
      { text: "I spoke with " },
      { text: "Acme", entity: { id: "c1", label: "Acme", kind: "company", docPath: "kg/acme.md" } },
      { text: " today" },
    ]);
  });

  it("matches whole words case-insensitively, not substrings", () => {
    const spans = splitTextIntoSpans("acme is not acmen", [E("Acme", "company")]);
    const matched = spans.filter((s) => s.entity);
    expect(matched).toHaveLength(1);
    expect(matched[0].text).toBe("acme");
    // the substring inside "acmen" must NOT be wrapped
    expect(spans.some((s) => !s.entity && s.text.includes("acmen"))).toBe(true);
  });

  it("highlights every occurrence of the same entity", () => {
    const spans = splitTextIntoSpans("Acme then Acme again", [E("Acme", "company")]);
    expect(spans.filter((s) => s.entity)).toHaveLength(2);
  });

  it("prefers the longest label when entities overlap", () => {
    const spans = splitTextIntoSpans("met Acme Corp", [E("Acme", "company"), E("Acme Corp", "company")]);
    const matched = spans.filter((s) => s.entity);
    expect(matched).toHaveLength(1);
    expect(matched[0].text).toBe("Acme Corp");
  });

  it("returns a single plain span when no entities are supplied (raw mode)", () => {
    expect(splitTextIntoSpans("plain text", [])).toEqual([{ text: "plain text" }]);
  });

  it("returns a single plain span when no label is found", () => {
    expect(splitTextIntoSpans("nothing here", [E("Acme", "company")])).toEqual([{ text: "nothing here" }]);
  });

  it("ignores too-short labels", () => {
    expect(splitTextIntoSpans("a b c", [E("a", "person")])).toEqual([{ text: "a b c" }]);
  });

  it("returns empty for empty text", () => {
    expect(splitTextIntoSpans("", [E("Acme")])).toEqual([]);
  });
});
