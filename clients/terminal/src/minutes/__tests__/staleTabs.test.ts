/** A TAB FOR A WORKSPACE THE CHAT DOES NOT HAVE IS DROPPED (Vexa-ai/vexa#1783, founder 2026-10-15:
 *  "I do not have Personal at all, I deleted it — why is it showing Personal to me?"). The panel kept
 *  a pinned "Personal" README for a chat that writes to biz and does not read Personal. A desk tab
 *  stays only while the chat reads or writes Personal; a tab of a hidden or gone workspace goes. */
import { describe, expect, it } from "vitest";
import { dropStaleTabs } from "../staleTabs";

const tabs = [
  { path: "README.md", label: "Personal", pinned: true },                       // the desk
  { path: "notes.md", label: "notes", pinned: true },                            // a desk page
  { path: "README.md", slug: "biz", label: "biz", pinned: true },
  { path: "plan.md", slug: "old", label: "plan", pinned: true },                // hidden / gone
  { kind: "meeting" as const, path: "meeting:120", label: "Meeting", permanent: true },
  { path: "README.md", slug: "_global", label: "Company" },
];

describe("dropStaleTabs", () => {
  it("drops the desk tabs when the chat neither reads nor writes Personal, and tabs of workspaces it does not have", () => {
    const out = dropStaleTabs(tabs, { reads: ["biz"], target: "biz", known: ["biz"] });
    expect(out.map((t) => `${t.slug ?? "desk"}:${t.path}`)).toEqual(["biz:README.md", "desk:meeting:120", "_global:README.md"]);
  });

  it("keeps the desk while the chat reads Personal", () => {
    const out = dropStaleTabs(tabs, { reads: ["personal", "biz"], target: "biz", known: ["personal", "biz"] });
    expect(out.filter((t) => !t.slug && t.kind !== "meeting").length).toBe(2);
  });

  it("returns the same array when nothing changes", () => {
    const keep = [tabs[2]];
    expect(dropStaleTabs(keep, { reads: ["biz"], target: "biz", known: ["biz"] })).toBe(keep);
  });
});
