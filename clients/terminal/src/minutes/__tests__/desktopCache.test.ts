/** "The app is not very reactive" (founder 2026-10-16): the home draws from the last answer at once,
 *  then refreshes. The cached answer is the same signed-in person's only. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadDesktop, resetDesktopStore, useDesktopState } from "../desktop/desktopApi";
import { renderHook, waitFor } from "@testing-library/react";

const desk = (n: string) => ({ version: 1, sections: [{ id: "workspaces", title: n, items: [] }], proposals: [], filters: [] });

afterEach(() => { resetDesktopStore(); localStorage.clear(); vi.unstubAllGlobals(); });

describe("the home from cache", () => {
  it("is drawn from the last answer at once, then replaced by the fresh one", async () => {
    localStorage.setItem("vexa.minutes.chatsOwner", "ann@x.io");
    localStorage.setItem("vexa.cache.desktop", JSON.stringify({ owner: "ann@x.io", desktop: desk("cached") }));
    let release!: () => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((res) => { release = () => res(new Response(JSON.stringify(desk("fresh")))); })));
    const { result } = renderHook(() => useDesktopState());
    await waitFor(() => expect(result.current.desktop?.sections[0].title).toBe("cached"));
    release();
    await waitFor(() => expect(result.current.desktop?.sections[0].title).toBe("fresh"));
    expect(JSON.parse(localStorage.getItem("vexa.cache.desktop")!).desktop.sections[0].title).toBe("fresh");
  });
  it("never draws another person's cached home", async () => {
    localStorage.setItem("vexa.minutes.chatsOwner", "bob@x.io");
    localStorage.setItem("vexa.cache.desktop", JSON.stringify({ owner: "ann@x.io", desktop: desk("anns") }));
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    void loadDesktop();
    const { result } = renderHook(() => useDesktopState(false));
    expect(result.current.desktop).toBeNull();
  });
});
