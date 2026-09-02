/** PRD decision 35 — the two intents a transcript mints, and what each refuses.
 *
 *  `explore` is a research drop about ONE thing somebody clicked; `highlight` is machinery with no
 *  words in it at all. Both go through the same door as `extend`/`create` (F63): a malformed intent
 *  becomes null and the caller sends nothing, because the failure this exists to prevent is the
 *  agent confidently working on a thing that was never in front of anybody.
 */
import { describe, expect, it } from "vitest";
import { isPageIntent, isSilent, normalizeIntent, TERM_MAX } from "../chatIntent";

describe("explore — a term clicked in a transcript", () => {
  it("carries the term, the meeting and the segment it was said in", () => {
    expect(normalizeIntent({ kind: "explore", term: " Kaar Tech ", meeting: "41", segment: "s7" }))
      .toEqual({ kind: "explore", term: "Kaar Tech", meeting: "41", segment: "s7" });
  });

  it("an absent segment stays ABSENT — provenance we do not have is not invented", () => {
    const i = normalizeIntent({ kind: "explore", term: "Kaar Tech", meeting: "41" })!;
    expect("segment" in i).toBe(false);
  });

  it("refuses a term with no meeting, and a meeting with no term", () => {
    expect(normalizeIntent({ kind: "explore", term: "Kaar Tech" })).toBeNull();
    expect(normalizeIntent({ kind: "explore", meeting: "41" })).toBeNull();
    expect(normalizeIntent({ kind: "explore", term: "   ", meeting: "41" })).toBeNull();
  });

  it("refuses a paragraph — a 'term' that long is a mis-click, and truncating it would ask the agent to research half a sentence", () => {
    expect(normalizeIntent({ kind: "explore", term: "x".repeat(TERM_MAX), meeting: "41" })).toBeTruthy();
    expect(normalizeIntent({ kind: "explore", term: "x".repeat(TERM_MAX + 1), meeting: "41" })).toBeNull();
  });

  it("is not a page intent — there is no path to land the panel on", () => {
    expect(isPageIntent(normalizeIntent({ kind: "explore", term: "a b", meeting: "41" })!)).toBe(false);
  });

  it("is NOT silent — the person clicked something and expects an answer about it", () => {
    expect(isSilent(normalizeIntent({ kind: "explore", term: "a b", meeting: "41" })!)).toBe(false);
  });
});

describe("highlight — the transcript's own button", () => {
  it("carries the meeting and the cursor the last publish issued", () => {
    expect(normalizeIntent({ kind: "highlight", meeting: "41", since: "c9" }))
      .toEqual({ kind: "highlight", meeting: "41", since: "c9" });
  });

  it("a first press has no cursor, and sends none", () => {
    expect(normalizeIntent({ kind: "highlight", meeting: "41" })).toEqual({ kind: "highlight", meeting: "41" });
  });

  it("refuses a press with no meeting", () => {
    expect(normalizeIntent({ kind: "highlight" })).toBeNull();
  });

  it("is silent — the person sees chips appear, never a bubble they did not type", () => {
    expect(isSilent(normalizeIntent({ kind: "highlight", meeting: "41" })!)).toBe(true);
  });
});

describe("the door is still closed to everything else", () => {
  it("an unknown kind is null, not a guess", () => {
    // @ts-expect-error — the point of the test is what happens when the type is bypassed
    expect(normalizeIntent({ kind: "explore-everything", term: "a b", meeting: "41" })).toBeNull();
  });

  it("a page intent still refuses a path that walks out of its mount", () => {
    expect(normalizeIntent({ kind: "extend", path: "../../etc/passwd" })).toBeNull();
  });
});
