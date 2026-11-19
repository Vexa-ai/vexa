import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within, act } from "@testing-library/react";
import { Rail } from "../Rail";
import { applyFolders, type RailFolders } from "../railFolders";
vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

// MANY CHATS AT ONCE (#1783) — founder 2026-10-13: "we want to be able to choose multiple chats at a
// time to move them or whatever".
const F: RailFolders = {
  served: true,
  folders: [
    { id: "pinned", name: "Pinned", builtin: true, collapsed: false },
    { id: "f-aaaaaaaaaaaa", name: "Clients", builtin: false, collapsed: false },
    { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
    { id: "hidden", name: "Hidden", builtin: true, collapsed: true },
  ],
  filings: {}, order: {},
};
const row = (key: string) => ({ key, chatId: key.slice(2), label: `Chat ${key}`, whenLabel: "1:00 PM" }) as any;
const ROWS = ["c:a", "c:b", "c:c", "c:d"].map(row);

function setup(over: Record<string, unknown> = {}) {
  const h = { move: vi.fn(), share: vi.fn(), delMany: vi.fn(), select: vi.fn() };
  const props: any = { rows: applyFolders(ROWS, F), folders: F, selKey: "c:a", onSelect: h.select, onNewChat: () => {},
    onDeleteChat: () => {}, onPin: () => {}, onHide: () => {}, onMoveRows: h.move, onShareChats: h.share, onDeleteChats: h.delMany, ...over };
  render(<Rail {...props} />);
  return h;
}
const btn = (key: string) => (document.querySelector(`[data-row-key="${key}"] [data-chat-row]`) as HTMLElement);
const picked = () => [...document.querySelectorAll("[data-picked]")].map((e) => e.getAttribute("data-row-key"));

describe("choosing several chats", () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("Cmd/Ctrl-click toggles (keeping the open chat), Shift-click takes a range, Esc clears", () => {
    const { select } = setup();
    fireEvent.click(btn("c:c"), { metaKey: true });
    expect(picked()).toEqual(["c:a", "c:c"]);
    fireEvent.click(btn("c:a"), { ctrlKey: true });
    expect(picked()).toEqual(["c:c"]);
    fireEvent.click(btn("c:b"), { shiftKey: true });          // from the last Cmd-clicked row (c:a)
    expect(picked()).toEqual(["c:a", "c:b"]);
    fireEvent.click(btn("c:d"), { shiftKey: true });
    expect(picked()).toEqual(["c:a", "c:b", "c:c", "c:d"]);
    expect(select).not.toHaveBeenCalled();
    expect(screen.getByRole("toolbar", { name: "Selected chats" }).textContent).toContain("4 selected");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(picked()).toEqual([]);
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("a plain click opens one chat and drops the selection", () => {
    const { select } = setup();
    fireEvent.click(btn("c:c"), { metaKey: true });
    fireEvent.click(btn("c:d"));
    expect(select).toHaveBeenCalledTimes(1);
    expect(picked()).toEqual([]);
  });

  it("dragging one chosen chat moves the whole selection", () => {
    const { move } = setup();
    fireEvent.click(btn("c:b"), { metaKey: true });
    fireEvent.click(btn("c:c"), { metaKey: true });
    const dt = { setData: () => {}, effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(document.querySelector('[data-row-key="c:c"]') as HTMLElement, { dataTransfer: dt });
    fireEvent.drop(document.querySelector('[data-section="f-aaaaaaaaaaaa"]') as HTMLElement, { dataTransfer: dt });
    expect(move).toHaveBeenCalledWith(["c:a", "c:b", "c:c"], "f-aaaaaaaaaaaa", undefined);
  });

  it("the action bar moves, shares and deletes the selection, with one confirmation naming the count", () => {
    const { move, share, delMany } = setup();
    fireEvent.click(btn("c:b"), { metaKey: true });
    const bar = () => screen.getByRole("toolbar", { name: "Selected chats" });
    fireEvent.click(within(bar()).getByRole("button", { name: "Move to…" }));
    expect(screen.getByRole("dialog", { name: "Move 2 chats to…" })).toBeTruthy();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Hidden" }));
    expect(move).toHaveBeenCalledWith(["c:a", "c:b"], "hidden", undefined);

    fireEvent.click(btn("c:c"), { metaKey: true });
    fireEvent.click(btn("c:d"), { metaKey: true });
    fireEvent.click(within(bar()).getByRole("button", { name: "Share…" }));
    fireEvent.change(screen.getByLabelText("Their email"), { target: { value: "pat@example.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Share" }));
    expect(share.mock.calls[0][0].map((r: any) => r.key)).toEqual(["c:a", "c:c", "c:d"]);
    expect(share.mock.calls[0][1]).toBe("pat@example.test");

    fireEvent.click(btn("c:b"), { metaKey: true });
    fireEvent.click(btn("c:c"), { metaKey: true });
    fireEvent.click(within(bar()).getByRole("button", { name: "Delete…" }));
    expect(screen.getByRole("dialog", { name: "Delete 3 chats?" })).toBeTruthy();
    expect(delMany).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete 3 chats" }));
    expect(delMany).toHaveBeenCalledWith(["a", "b", "c"]);
  });

  it("the bar fits the rail: icon buttons named by their tooltips; Delete is not red", () => {
    setup();
    fireEvent.click(btn("c:b"), { metaKey: true });
    const bar = screen.getByRole("toolbar", { name: "Selected chats" });
    const del = within(bar).getByRole("button", { name: "Delete…" });
    expect(del.getAttribute("title")).toBe("Delete…");
    expect(del.getAttribute("data-variant")).not.toBe("danger");
    expect(bar.textContent).toBe("2 selected");                 // no words beside the count
  });

  it("a chosen row's menu acts on the whole selection", () => {
    setup();
    fireEvent.click(btn("c:b"), { metaKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Actions for Chat c:b" }));
    expect(screen.getByRole("menuitem", { name: /Move 2 chats to/ })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /Delete 2 chats/ })).toBeTruthy();
  });

  it("on a phone a long press starts picking: checkboxes show and a tap toggles instead of opening", () => {
    vi.useFakeTimers();
    const { select } = setup({ selKey: null });
    const b = document.querySelector('[data-row-key="c:b"]') as HTMLElement;
    fireEvent.touchStart(b, { touches: [{ clientX: 10, clientY: 10 }] });
    act(() => { vi.advanceTimersByTime(500); });
    fireEvent.touchEnd(b, { changedTouches: [{ clientX: 10, clientY: 10 }] });
    expect(picked()).toEqual(["c:b"]);
    // the picked row carries a small check; the others an empty checkbox
    expect(document.querySelectorAll(".vx-rail-check").length).toBe(3);
    expect(document.querySelectorAll(".vx-rail-picked").length).toBe(1);
    fireEvent.click(btn("c:d"));
    expect(picked()).toEqual(["c:b", "c:d"]);
    expect(select).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(document.querySelectorAll(".vx-rail-check").length).toBe(0);
  });
});
