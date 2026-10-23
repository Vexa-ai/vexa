/** THE PANEL'S WORKSPACE TAB FOLLOWS THE CHAT'S TARGET (Vexa-ai/vexa#1783, founder 2026-10-14: a tab
 *  "biz-b1c87fc32ecb" sat permanently before README while the chat wrote to AI Tinkerers Lisbon —
 *  "what is this tab doing here — it's irreplaceable and out of context").
 *
 *  It was the chat's FIRST mounted workspace, labelled by slug and marked `desk` (uncloseable). Now:
 *  the home is the README of where the chat WRITES, named, and closable like any tab. */
import { describe, expect, it, vi } from "vitest";

vi.mock("../../ui-kit/wsLinks", () => ({
  workspaceLabel: (s: string) => ({ "ai-tinkerers": "AI Tinkerers Lisbon", "biz-b1c87fc32ecb": "biz" } as Record<string, string>)[s] ?? s,
  primeWorkspaceNames: async () => undefined,
  workspaceBySlug: async () => null,
}));
vi.mock("../../data/workspaceApi", () => ({ readActiveSet: async () => ({ subject: "56", active: [] }) }));

import { homeEntry, withHome, type Artifact } from "../chats";
import { deskPanelPages } from "../deskPanel";

const reads = ["personal", "_global", "biz-b1c87fc32ecb", "ai-tinkerers"];

describe("the chat's home tab", () => {
  it("is the README of where the chat writes, by name, not its first mount by slug", () => {
    const h = homeEntry(reads, "ai-tinkerers");
    expect(h.slug).toBe("ai-tinkerers");
    expect(h.label).toBe("AI Tinkerers Lisbon");
  });

  it("is Personal when the chat writes to Personal, whatever else it reads", () => {
    expect(homeEntry(reads, "").slug).toBeUndefined();
  });

  it("can be closed: it is neither the desk nor permanent", () => {
    const h = homeEntry(reads, "ai-tinkerers");
    expect(h.desk).toBeFalsy();
    expect(h.permanent).toBeFalsy();
  });

  it("is replaced when the target moves, and a stale permanent tab from before is dropped", () => {
    const legacy: Artifact = { path: "README.md", slug: "biz-b1c87fc32ecb", label: "biz-b1c87fc32ecb", desk: true };
    const strip = withHome([legacy, { path: "notes.md", slug: "ai-tinkerers", label: "notes", pinned: true, at: 3 }], reads, "ai-tinkerers");
    expect(strip.map((a) => a.slug ?? "personal")).toEqual(["ai-tinkerers", "ai-tinkerers"]);
    expect(strip.some((a) => a.slug === "biz-b1c87fc32ecb")).toBe(false);
    const moved = withHome(strip, reads, "");
    expect(moved[0].slug).toBeUndefined();
    expect(moved.filter((a) => a.path === "README.md" && a.slug === "ai-tinkerers")).toEqual([]);
  });

  it("a fresh chat's panel opens Personal and its target — not a README per mounted workspace", async () => {
    const pages = await deskPanelPages(reads, "ai-tinkerers");
    expect(pages.map((p) => p.slug ?? "personal")).toEqual(["personal", "ai-tinkerers"]);
  });
});
