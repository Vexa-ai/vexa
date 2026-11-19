/** UI-1 step 5 (feature `shell2`): the phone — the bottom bar (decision 10), system back closing the
 *  top layer (G2), a list's history entry handed over to the drawer without a racing pop. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { BottomBar } from "../BottomBar";
import { centreNow, closeCentre, openCentre, resetCentre } from "../centre";
import { useBackCloses } from "../useBackCloses";

afterEach(() => { cleanup(); resetCentre(); });

describe("the bottom bar (single mode)", () => {
  it("is Home · Inbox · Meetings · People; the chats stay in the drawer (header menu control)", () => {
    const onHome = vi.fn();
    render(<BottomBar onHome={onHome} home chatsOpen={false} />);
    expect([...document.querySelectorAll("[data-tab-key]")].map((e) => e.getAttribute("data-tab-key"))).toEqual(["home", "inbox", "meetings", "people"]);
    fireEvent.click(screen.getByRole("button", { name: /^Meetings/ }));
    expect(centreNow()).toBe("meetings");
    fireEvent.click(screen.getByRole("button", { name: "People" }));
    expect(centreNow()).toBe("people");
    fireEvent.click(screen.getByRole("button", { name: "Home" }));
    expect(onHome).toHaveBeenCalled();
  });
});

describe("system back", () => {
  it("a centre list pushes one entry; back closes it", () => {
    openCentre("inbox");
    expect((window.history.state as Record<string, unknown>).vexaCentre).toBe("inbox");
    act(() => { window.dispatchEvent(new PopStateEvent("popstate", { state: {} })); });
    expect(centreNow()).toBeNull();
  });
  it("a list handed over to another layer is marked, never popped", () => {
    const back = vi.spyOn(window.history, "back");
    openCentre("meetings");
    closeCentre(false, true);
    expect(back).not.toHaveBeenCalled();
    expect((window.history.state as Record<string, unknown> | null)?.vexaCentre).toBeUndefined();
    back.mockRestore();
  });
  it("an open overlay closes on back", () => {
    const close = vi.fn();
    renderHook(() => useBackCloses(true, close, "sheet"));
    expect((window.history.state as Record<string, unknown>).vexaLayer).toBe("sheet");
    act(() => { window.dispatchEvent(new PopStateEvent("popstate", { state: {} })); });
    expect(close).toHaveBeenCalledTimes(1);
  });
});
