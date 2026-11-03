/** THE RICH PAGE GRAMMAR (#1783, R3 `home2`; BUILD 2026-10-16 "Rich README"): markdown plus typed
 *  references, badges, blocks, actions from a fixed allow-list and ```view blocks. No code in pages:
 *  raw HTML is sanitized, an action outside the allow-list is plain text, a view source outside the
 *  list renders nothing. These are the pure rules; the renderer only draws them. */
import { describe, expect, it } from "vitest";
import {
  ACTION_VERBS, VIEW_SOURCES, hasRichSyntax, parseAction, parseBlocks, parseRefs, parseView, sanitizeHtml,
  splitInline, splitFrontMatter, actionNeedsConfirm,
} from "../desktop/home2/richParse";

describe("sanitizeHtml", () => {
  it("drops scripts, frames, styles, handlers and javascript: links", () => {
    const md = 'a<script>alert(1)</script>b<iframe src="x"></iframe><img src=x onerror="steal()"><a href="javascript:alert(1)">x</a><style>p{}</style><svg onload=alert(1)></svg>';
    const out = sanitizeHtml(md);
    expect(out).not.toMatch(/script|iframe|onerror|onload|javascript:|<style/i);
    expect(out).toContain("a");
    expect(out).toContain("b");
  });
  it("leaves fenced code untouched (a snippet showing <script> is text, not HTML)", () => {
    const md = "```html\n<script>x</script>\n```";
    expect(sanitizeHtml(md)).toBe(md);
  });
  it("drops an unclosed script and everything after it on the line", () => {
    expect(sanitizeHtml("x <script src=evil.js>")).not.toMatch(/script/i);
  });
});

describe("splitFrontMatter", () => {
  it("reads simple key: value lines and returns the body", () => {
    const { meta, body } = splitFrontMatter("---\ntitle: Plan\nmaintained_by: routine:growth\n---\n# Hi\n");
    expect(meta).toEqual({ title: "Plan", maintained_by: "routine:growth" });
    expect(body).toBe("# Hi\n");
  });
});

describe("parseRefs", () => {
  it("finds every typed reference kind, de-duplicated, with an optional label", () => {
    const refs = parseRefs("[[room:r1]] and [[chat:c1|Mine]] [[call:12]] [[file:notes/a.md]] [[person:u7]] [[card:k1]] [[routine:flow:x]] [[workspace:alpha]] [[room:r1]]");
    expect(refs.map((r) => r.key)).toEqual(["room:r1", "chat:c1", "call:12", "file:notes/a.md", "person:u7", "card:k1", "routine:flow:x", "workspace:alpha"]);
    expect(refs[1].label).toBe("Mine");
  });
  it("ignores untyped wikilinks and unknown kinds", () => {
    expect(parseRefs("[[Plan]] [[secret:x]] [[page:a/b]]")).toEqual([]);
  });
});

describe("parseAction", () => {
  it("reads a verb, a target and a label from the allow-list", () => {
    expect(parseAction("[[action:run-routine:flow:growth | Run now]]")).toEqual({ verb: "run-routine", target: "flow:growth", label: "Run now" });
    expect(parseAction("[[action:new-chat: | New chat]]")).toEqual({ verb: "new-chat", target: "", label: "New chat" });
  });
  it("refuses a verb outside the allow-list", () => {
    expect(parseAction("[[action:delete-workspace:alpha | Delete]]")).toBeNull();
    expect(parseAction("[[action:fetch:https://evil | x]]")).toBeNull();
  });
  it("is the one verb registry: the founder's allow-list plus the README actions (D17)", () => {
    expect([...ACTION_VERBS].sort()).toEqual(["accept-proposal", "ask", "invite", "join-call", "move-card", "new-chat", "new-room",
      "prepare", "run", "run-routine", "schedule", "schedule-call", "start-call"]);
  });
  it("asks confirmation for anything with an external effect", () => {
    expect(actionNeedsConfirm("run-routine")).toBe(true);
    expect(actionNeedsConfirm("schedule-call")).toBe(true);
    expect(actionNeedsConfirm("start-call")).toBe(true);
    expect(actionNeedsConfirm("accept-proposal")).toBe(true);
    expect(actionNeedsConfirm("move-card")).toBe(true);
    expect(actionNeedsConfirm("new-chat")).toBe(false);
    expect(actionNeedsConfirm("prepare")).toBe(false);
    expect(actionNeedsConfirm("join-call")).toBe(false);
  });
});

describe("parseView", () => {
  it("reads the spec of a ```view block", () => {
    expect(parseView("source: crm.card\nas: board\ngroup: stage\ncolumns: title, stage, next\nfilter: workspace=alpha")).toEqual({
      source: "crm.card", as: "board", group: "stage", columns: ["title", "stage", "next"], filter: { workspace: "alpha" }, folder: undefined, limit: 50,
    });
  });
  it("defaults to a table and refuses a source outside the list", () => {
    expect(parseView("source: pages\nfolder: projects")).toMatchObject({ source: "pages", as: "table", folder: "projects" });
    expect(parseView("source: sql\nquery: drop table")).toBeNull();
    expect([...VIEW_SOURCES].sort()).toEqual(["crm.card", "meetings", "pages", "rooms"]);
  });
  it("caps the limit", () => {
    expect(parseView("source: rooms\nlimit: 100000")?.limit).toBe(200);
  });
});

describe("parseBlocks", () => {
  it("splits columns, callouts, stats, view fences and reference lines from plain markdown", () => {
    const md = [
      "# Title", "intro", ":::columns", "left", "---", "right [[room:r1]]", ":::",
      ":::callout", "Heads up", ":::", ":::stats source=data/kpi.json", ":::",
      "```view", "source: rooms", "```", "after {{needs-you}}", "| a | b |", "|---|---|", "| 1 | 2 |",
    ].join("\n");
    const b = parseBlocks(md);
    expect(b.map((x) => x.t)).toEqual(["md", "columns", "callout", "stats", "view", "line", "md"]);
    const cols = b[1] as { t: "columns"; cols: { t: string }[][] };
    expect(cols.cols).toHaveLength(2);
    expect(cols.cols[1][0].t).toBe("line");
    expect((b[3] as { source: string }).source).toBe("data/kpi.json");
    expect((b[4] as { spec: { source: string } }).spec.source).toBe("rooms");
  });
  it("never treats a reference inside a code fence as live", () => {
    const b = parseBlocks("```\n[[room:r1]]\n```");
    expect(b.map((x) => x.t)).toEqual(["md"]);
  });
  it("an unclosed block runs to the end of the page", () => {
    expect(parseBlocks(":::callout\nopen").map((x) => x.t)).toEqual(["callout"]);
  });
});

describe("splitInline", () => {
  it("splits a line into text, references, badges and actions in order", () => {
    const parts = splitInline("- See [[room:r1]] then {{status}} or [[action:new-chat: | Ask]] done");
    expect(parts.map((p) => p.t)).toEqual(["text", "ref", "text", "badge", "text", "action", "text"]);
    expect(parts[0]).toEqual({ t: "text", text: "See " });
  });
  it("keeps a refused action as its plain label", () => {
    const parts = splitInline("[[action:rm:x | Remove]]");
    expect(parts).toEqual([{ t: "text", text: "Remove" }]);
  });
});

describe("hasRichSyntax", () => {
  it("is true only when a page uses the rich grammar", () => {
    expect(hasRichSyntax("# plain\n\ntext [[Plan]]")).toBe(false);
    expect(hasRichSyntax("x [[room:r1]]")).toBe(true);
    expect(hasRichSyntax("{{members}}")).toBe(true);
    expect(hasRichSyntax(":::callout\nx\n:::")).toBe(true);
    expect(hasRichSyntax("```view\nsource: rooms\n```")).toBe(true);
    expect(hasRichSyntax("[[action:new-chat: | Ask]]")).toBe(true);
    expect(hasRichSyntax("```\n[[room:r1]]\n```")).toBe(false);
  });
});

import { setFrontMatterField } from "../desktop/home2/richParse";
describe("setFrontMatterField (a view block's inline edit through the page write)", () => {
  it("replaces, adds, or creates the field and keeps the body", () => {
    expect(setFrontMatterField("---\nstatus: draft\n---\nBody", "status", "done")).toBe("---\nstatus: done\n---\nBody");
    expect(setFrontMatterField("---\nstatus: draft\n---\nBody", "owner", "Ann")).toBe("---\nstatus: draft\nowner: Ann\n---\nBody");
    expect(setFrontMatterField("Body", "status", "done")).toBe("---\nstatus: done\n---\nBody");
  });
  it("cannot break out of the block", () => {
    const out = setFrontMatterField("---\na: 1\n---\nBody", "a", "x\n---\nevil: 1");
    expect(splitFrontMatter(out).meta).toEqual({ a: "x --- evil: 1" });
  });
});
