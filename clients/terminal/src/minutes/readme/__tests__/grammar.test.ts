/** The one page grammar, client half: the shared cases the server's parser also runs (D17/D18). */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ACCEPTED_VERBS, blocksIn, canonicalVerb, parseRefParts, refChip, refUrl, refsIn, verbsIn } from "../grammar";

const CASES = resolve(__dirname, "../../../../../../core/agent/control_plane/page_grammar.cases.json");
type Case = { src: string; refs: string[]; blocks: string[]; verbs: string[] };
const cases: Case[] = existsSync(CASES) ? JSON.parse(readFileSync(CASES, "utf8")).cases : [];

describe("page grammar (shared cases with page_grammar.py)", () => {
  it("has the cases file", () => { expect(cases.length).toBeGreaterThan(10); });
  for (const c of cases) {
    it(c.src.slice(0, 50), () => {
      expect(refsIn(c.src).map((r) => r.key)).toEqual(c.refs);
      expect(blocksIn(c.src).map((b) => b.key)).toEqual(c.blocks);
      expect(verbsIn(c.src)).toEqual(c.verbs);
    });
  }
});

describe("one verb registry", () => {
  it("holds both old verb sets", () => {
    for (const v of ["run-routine", "start-call", "join-call", "new-chat", "accept-proposal", "prepare", "move-card", "schedule-call",
      "new-room", "schedule", "invite", "ask", "run"]) expect(ACCEPTED_VERBS).toContain(v);
    expect(canonicalVerb("run-routine")).toBe("run");
    expect(canonicalVerb("rm")).toBeNull();
  });
});

describe("anchors", () => {
  it("round-trips a meeting moment", () => {
    const r = parseRefParts("meeting", "153@0:12:30")!;
    expect(r.key).toBe("meeting:153#t=00:12:30");
    expect(refChip(r)).toBe("[[meeting:153@00:12:30]]");
    expect(refUrl(r)).toBe("/r/meeting/153#t=00:12:30");
  });
});
