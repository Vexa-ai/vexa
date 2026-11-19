import { describe, it, expect, vi, beforeEach } from "vitest";
import { currentUserPins, loadUserPins, pinPage, resetUserPins, unpinPage, withUserPins } from "../userPins";

// PINNED PAGES ARE THE PERSON'S (#1783; founder: "pinned pages are in every session").
const unanswered = { slug: null, path: "inbox/unanswered.md", label: "Unanswered" };
const growth = { slug: "biz", path: "graph/sg/Growth.md", label: "Growth" };
const desk = { kind: "doc", path: "README.md", label: "Personal", desk: true } as any;

let calls: { method: string; body?: string }[] = [];
function server(pages: unknown[]) {
  calls = [];
  return vi.fn(async (_u: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", body: init?.body as string | undefined });
    return new Response(JSON.stringify({ pages }), { status: 200 });
  }) as unknown as typeof fetch;
}
beforeEach(() => { localStorage.clear(); resetUserPins(); });

describe("one list, every chat", () => {
  it("a chat that never saw the pin gets it as a tab, in the person's order", () => {
    const tabs = withUserPins([desk, { kind: "doc", path: "notes/a.md", label: "A", at: 5 } as any], [growth, unanswered]);
    expect(tabs.map((t) => [t.path, !!t.pinned])).toEqual([
      ["README.md", false], ["graph/sg/Growth.md", true], ["inbox/unanswered.md", true], ["notes/a.md", false]]);
  });
  it("unpinning anywhere unpins it in a chat that kept its own copy; a meeting's own pins are left alone", async () => {
    vi.stubGlobal("fetch", server([]));
    await pinPage(unanswered);
    await unpinPage(unanswered);
    const tabs = withUserPins([{ kind: "doc", path: "inbox/unanswered.md", label: "U", pinned: true } as any,
      { kind: "meeting", path: "147", label: "Weekly", pinned: true } as any], currentUserPins());
    expect(tabs.find((t) => t.path === "inbox/unanswered.md")?.pinned).toBeFalsy();
    expect(tabs.find((t) => t.path === "147")?.pinned).toBe(true);
  });
  it("loads the server's list; with none there, the open chat's pins are kept and saved", async () => {
    await loadUserPins([unanswered], server([growth]));
    expect(currentUserPins()).toEqual([growth]);
    resetUserPins();
    await loadUserPins([unanswered], server([]));
    expect(currentUserPins()).toEqual([unanswered]);
    expect(calls.some((c) => c.method === "PUT" && c.body?.includes("inbox/unanswered.md"))).toBe(true);
  });
});
