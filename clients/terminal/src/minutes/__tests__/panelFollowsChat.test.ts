/** THE RIGHT PANEL FOLLOWS THE CHAT IN THE CENTRE (founder 2026-10-16: after a reload a chat that
 *  writes to AI Tinkerers Lisbon showed the Citizenship applications README and a stray README tab).
 *  It shows what this chat last opened, else this chat's target workspace page, else nothing; tabs
 *  from other chats never come along. */
import { describe, expect, it } from "vitest";
import { panelForChat } from "../roomView";
import { pinsForPanel } from "../userPins";

const readme = { path: "README.md", slug: "citz", label: "Citizenship applications" };

describe("panelForChat", () => {
  it("opens what the chat last had in front", () => {
    const mine = { path: "plan.md", slug: "ait", label: "plan" };
    expect(panelForChat({ list: [mine], front: mine, target: "ait", targetName: "AI Tinkerers Lisbon" }))
      .toEqual({ list: [mine], front: mine });
  });
  it("defaults to the chat's target workspace page", () => {
    const r = panelForChat({ list: [], front: null, target: "ait", targetName: "AI Tinkerers Lisbon" });
    expect(r.front).toEqual({ path: ".workspace", slug: "ait", label: "AI Tinkerers Lisbon" });
    expect(r.list).toEqual([r.front]);
  });
  it("stays closed with nothing of its own and no target", () => {
    expect(panelForChat({ list: [], front: null, target: "", targetName: "" })).toEqual({ list: [], front: null });
    expect(panelForChat({ list: [], front: null, target: "personal", targetName: "Personal" })).toEqual({ list: [], front: null });
  });
  it("never brings another chat's pinned tab along (home2)", () => {
    const pins = [{ path: "README.md", slug: "citz", label: "Citizenship applications" }] as never[];
    expect(pinsForPanel([], pins, true)).toEqual([]);
    expect(pinsForPanel([], pins, false).map((p) => p.slug)).toEqual(["citz"]);
  });
});
