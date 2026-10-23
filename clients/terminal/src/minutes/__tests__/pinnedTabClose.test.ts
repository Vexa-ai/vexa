/** Founder 2026-10-16: a PINNED tab could not be removed — × did nothing and unpinning did not stick
 *  (the pinned-pages store answered 404). × on a pinned tab unpins and closes in one act; the pin
 *  lives locally when the store is not there. */
import { describe, expect, it, vi } from "vitest";
import { closingTab } from "../chats";
import { currentUserPins, pinPage, unpinPage, withUserPins } from "../userPins";

const tab = { path: "README.md", slug: "citz", label: "Citizenship applications", pinned: true } as never;

describe("closing a pinned tab", () => {
  it("unpins and closes in one act, and the pin never brings it back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("not found", { status: 404 })));
    await pinPage({ path: "README.md", slug: "citz", label: "Citizenship applications" });
    expect(currentUserPins()).toHaveLength(1);
    const { next, unpin } = closingTab([tab, { path: "plan.md", label: "plan" } as never], tab);
    expect(unpin).toBe(true);
    expect(next.map((a: { path: string }) => a.path)).toEqual(["plan.md"]);
    await unpinPage({ path: "README.md", slug: "citz" });
    expect(currentUserPins()).toHaveLength(0);                       // the store 404d; the pin is gone locally
    expect(withUserPins(next, currentUserPins()).map((a: { path: string }) => a.path)).toEqual(["plan.md"]);
    vi.unstubAllGlobals();
  });
  it("an unpinned tab just closes", () => {
    const t = { path: "a.md", label: "a" } as never;
    expect(closingTab([t], t)).toEqual({ next: [], unpin: false });
  });
});
