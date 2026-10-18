/** What a transcript line shows from its signals: a chip for a WAKE label scored at or over the
 *  threshold, and at most a few emphasised words. Pure, so the rules are pinned without a screen. */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { chipsFor, emphasisWords, labelText, splitEmphasis, wakeLabels, DEFAULT_CHIP_THRESHOLD, marksFor } from "../signalMarks";

const ann = (scores: Record<string, number>, words: Record<string, number> = {}) => ({ id: "1-0", segment_id: "s1", scores, words });
const brief = { focus: "install blockers", labels: [
  { name: "blocker", wake: true }, { name: "decision", wake: true }, { name: "new_name", wake: false },
] };

describe("chips", () => {
  it("names the four wake labels the way a person reads them", () => {
    expect(labelText("decision")).toBe("Decision");
    expect(labelText("action_item")).toBe("Action");
    expect(labelText("blocker")).toBe("Blocker");
    expect(labelText("addressed_to_vexa")).toBe("To Vexa");
    expect(labelText("budget_risk")).toBe("Budget risk");
  });
  it("shows only wake labels at or over the threshold; to-Vexa always wakes", () => {
    const c = chipsFor(ann({ blocker: 0.9, decision: DEFAULT_CHIP_THRESHOLD, new_name: 0.99, addressed_to_vexa: 0.8, action_item: 0.95 }), brief);
    expect(c.map((x) => x.name)).toEqual(["blocker", "addressed_to_vexa", "decision"]);
  });
  it("below the threshold there is no chip", () => {
    expect(chipsFor(ann({ blocker: DEFAULT_CHIP_THRESHOLD - 0.01 }), brief)).toEqual([]);
  });
  it("the brief's own threshold wins over the default", () => {
    expect(chipsFor(ann({ blocker: 0.5 }), { ...brief, threshold: 0.4 }).map((x) => x.name)).toEqual(["blocker"]);
  });
  it("without a brief the standard wake labels apply", () => {
    expect(wakeLabels(undefined)).toEqual(["decision", "action_item", "addressed_to_vexa"]);
    expect(wakeLabels(brief)).toEqual(["blocker", "decision", "addressed_to_vexa"]);
  });
});

describe("word emphasis", () => {
  it("takes at most three significant words, most significant first", () => {
    const w = emphasisWords(ann({}, { Friday: 0.9, rollout: 0.8, Harbor: 0.75, proxy: 0.72, the: 0.95, a: 0.99, maybe: 0.3 }));
    expect(w).toEqual(["friday", "rollout", "harbor"]);
  });
  it("splits text on whole words, case-insensitively", () => {
    expect(splitEmphasis("Ship it Friday, not friday-ish.", ["friday"])).toEqual([
      { text: "Ship it ", strong: false }, { text: "Friday", strong: true }, { text: ", not ", strong: false },
      { text: "friday", strong: true }, { text: "-ish.", strong: false },
    ]);
    expect(splitEmphasis("nothing here", [])).toEqual([{ text: "nothing here", strong: false }]);
  });
});

describe("marksFor", () => {
  it("answers per segment, and nothing for a line without an annotation", () => {
    const m = marksFor({ s1: ann({ decision: 0.9 }, { Friday: 0.9 }) }, brief);
    expect(m("s1")).toEqual({ chips: [{ name: "decision", label: "Decision", score: 0.9 }], words: ["friday"] });
    expect(m("s2")).toBeUndefined();
    expect(m(undefined)).toBeUndefined();
  });
});

describe("the default wake labels are agent-api's DEFAULT_BRIEF", () => {
  it("blocker is an example the agent may add, never a default: no brief, no Blocker chip", () => {
    expect(chipsFor(ann({ blocker: 0.99 }), undefined)).toEqual([]);
  });
  it("matches core/agent/control_plane/meeting_listen.py DEFAULT_BRIEF's waking labels", () => {
    const py = readFileSync(join(__dirname, "../../../../../core/agent/control_plane/meeting_listen.py"), "utf8");
    const block = py.slice(py.indexOf("DEFAULT_BRIEF = {"), py.indexOf("\n}\n", py.indexOf("DEFAULT_BRIEF = {")));
    const names = [...block.matchAll(/"name":\s*("([a-z_]+)"|ADDRESSED)/g)].map((m) => m[2] ?? "addressed_to_vexa");
    expect(names.length).toBeGreaterThan(0);
    expect(wakeLabels(undefined)).toEqual(names);
  });
});
