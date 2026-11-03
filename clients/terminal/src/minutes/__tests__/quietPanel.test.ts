/** THE RIGHT PANEL STAYS QUIET (Vexa-ai/vexa#1783, UI decision 7 amended, founder 2026-10-15: "let's
 *  not render anything in the right sidebar until things are delivered in chat to click or we are in
 *  a meeting"). It opens on a click in the chat, a live meeting, or Files — never by itself. */
import { describe, expect, it } from "vitest";
import { autoOpens, openingStrip, panelShows } from "../staleTabs";

describe("what the panel opens by itself", () => {
  it("a file the agent wrote is a chip in the chat, not a tab", () => {
    expect(autoOpens({ path: "notes/plan.md", workspace: "biz", focus: true })).toBe(false);
  });
  it("a meeting the chat just started opens (the live meeting is pinned)", () => {
    expect(autoOpens({ path: "meeting:120", focus: true })).toBe(true);
  });
});

describe("the strip a chat opens with", () => {
  it("a new chat opens with nothing — no desk, no workspace README", () => {
    expect(openingStrip([], { meeting: false })).toEqual([]);
  });
  it("a stored strip keeps the tabs the person opened and drops the old auto-opened home/desk tabs", () => {
    const stored = [
      { path: "README.md", label: "Personal", desk: true },
      { path: "README.md", slug: "biz", label: "biz", home: true, pinned: true },
      { path: "plan.md", slug: "biz", label: "plan", pinned: true },
    ];
    expect(openingStrip(stored, { meeting: false }).map((t) => t.path)).toEqual(["plan.md"]);
  });
  it("a meeting chat keeps its meeting's pages", () => {
    const stored = [{ kind: "meeting" as const, path: "meeting:120", label: "Meeting", permanent: true }];
    expect(openingStrip(stored, { meeting: true })).toEqual(stored);
  });
});

describe("whether the panel shows at all", () => {
  it("no tab and no Files listing: the panel is not drawn", () => {
    expect(panelShows(0, false)).toBe(false);
    expect(panelShows(1, false)).toBe(true);
    expect(panelShows(0, true)).toBe(true);
  });
});
