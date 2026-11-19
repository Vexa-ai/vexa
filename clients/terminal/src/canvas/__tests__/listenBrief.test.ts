/** The listening brief lives in the meeting page's front matter (`listen:`). People edit it through
 *  the page: what they change is written back into that block, the rest of the page untouched, the
 *  version bumped, and "addressed to Vexa" can never be removed. */
import { describe, expect, it } from "vitest";
import { parseDocument } from "yaml";
import { readListenBrief, writeListenBrief } from "../listenBrief";

const PAGE = [
  "---",
  "type: meeting",
  "meeting: 147",
  "title: Install review",
  "listen:",
  "  focus: \"Install blockers: proxy, sign-in, TLS; decisions on dates\"",
  "  labels:",
  "    - {name: blocker, question: \"Is something blocking the install?\", wake: true}",
  "    - {name: decision, wake: true}",
  "    - {name: addressed_to_vexa, wake: true}",
  "    - {name: new_name, wake: false}",
  "  known: \"Proxy fixed on Tuesday.\"",
  "  wake_at: 1.0",
  "  max_wait_s: 300",
  "  version: 4",
  "---",
  "",
  "# Install review",
  "",
  "<!-- vexa:transcript meeting=147 -->",
  "",
].join("\n");

describe("readListenBrief", () => {
  it("reads the block", () => {
    const b = readListenBrief(PAGE)!;
    expect(b.focus).toMatch(/^Install blockers/);
    expect(b.labels.map((l) => l.name)).toEqual(["blocker", "decision", "addressed_to_vexa", "new_name"]);
    expect(b.labels[0]).toEqual({ name: "blocker", question: "Is something blocking the install?", wake: true });
    expect(b.version).toBe(4);
    expect(b.wake_at).toBe(1);
  });
  it("is null for a page without one, or without front matter", () => {
    expect(readListenBrief("---\ntitle: x\n---\n# x\n")).toBeNull();
    expect(readListenBrief("# just a page\n")).toBeNull();
    expect(readListenBrief("---\nlisten: [not, a, map]\n---\n")).toBeNull();
  });
});

describe("writeListenBrief", () => {
  it("writes the edit into the block, bumps the version and leaves the rest of the page alone", () => {
    const b = readListenBrief(PAGE)!;
    const next = writeListenBrief(PAGE, { ...b, focus: "Dates only", labels: [...b.labels, { name: "budget", question: "Is money discussed?", wake: true }] });
    const back = readListenBrief(next)!;
    expect(back.focus).toBe("Dates only");
    expect(back.labels.map((l) => l.name)).toContain("budget");
    expect(back.version).toBe(5);
    expect(back.known).toBe("Proxy fixed on Tuesday.");
    expect(back.max_wait_s).toBe(300);
    expect(next.endsWith("# Install review\n\n<!-- vexa:transcript meeting=147 -->\n")).toBe(true);
    const fm = parseDocument(next.split("---")[1]).toJS();
    expect(fm.title).toBe("Install review");
    expect(fm.meeting).toBe(147);
  });
  it("puts addressed-to-Vexa back if an edit removed it, waking", () => {
    const b = readListenBrief(PAGE)!;
    const next = writeListenBrief(PAGE, { ...b, labels: b.labels.filter((l) => l.name !== "addressed_to_vexa") });
    const l = readListenBrief(next)!.labels.find((x) => x.name === "addressed_to_vexa");
    expect(l?.wake).toBe(true);
  });
  it("drops a label with no name and normalises names", () => {
    const b = readListenBrief(PAGE)!;
    const next = writeListenBrief(PAGE, { ...b, labels: [...b.labels, { name: "  " }, { name: "Next Steps!" }] });
    const names = readListenBrief(next)!.labels.map((l) => l.name);
    expect(names).toContain("next_steps");
    expect(names).not.toContain("");
  });
});
