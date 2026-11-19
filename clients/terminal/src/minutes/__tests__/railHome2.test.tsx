/** R1 — the sidebar by workspace (home2) and the room entry points (rooms). Pinned · one group per
 *  workspace (rooms first, then your chats, then calls) · Hidden; no folder UI; an empty workspace
 *  group is drawn only while a chat is being dragged; New room and Browse open rooms in the header. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Rail } from "../Rail";
import { applyFolders, type RailFolders } from "../railFolders";
import { setFeatureOverride } from "../../platform/features";
vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

const F: RailFolders = {
  served: true,
  folders: [
    { id: "pinned", name: "Pinned", builtin: true, collapsed: false },
    { id: "ws:personal", name: "Personal", builtin: false, collapsed: false },
    { id: "ws:product", name: "Product planning", builtin: false, collapsed: false },
    { id: "ws:garden", name: "Garden club", builtin: false, collapsed: false },
    { id: "hidden", name: "Hidden", builtin: true, collapsed: true },
  ],
  filings: { "c:chat1": "ws:product", "c:room1": "ws:product", "c:call1": "ws:product", "c:mine": "ws:personal" },
  order: {},
};
const row = (key: string, extra: Record<string, unknown> = {}) => ({ key, chatId: key.slice(2), label: `Row ${key}`, whenLabel: "1:00 PM", ...extra }) as never;
const rows = () => applyFolders([row("c:chat1"), row("c:call1", { kind: "call" }), row("c:room1", { shared: true }), row("c:mine")], F);

afterEach(() => { cleanup(); setFeatureOverride("all"); });

describe("the sidebar by workspace (home2)", () => {
  it("is Pinned, then Recent (the newest chats across everything), then the person's folders, then the rest and Hidden (UI 7e)", () => {
    setFeatureOverride({ home2: true, rooms: true });
    const FF: RailFolders = { served: true, order: {},
      folders: [{ id: "pinned", name: "Pinned", builtin: true, collapsed: false }, { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
        { id: "f-launch", name: "Launch", builtin: false, collapsed: false }, { id: "hidden", name: "Hidden", builtin: true, collapsed: true }],
      filings: { "c:old": "f-launch", "c:new": "f-launch" } };
    const many = Array.from({ length: 10 }, (_, i) => row(`c:n${i}`, { when: 100 + i }));
    const all = applyFolders([row("c:old", { when: 1 }), row("c:new", { when: 999 }), ...many], FF);
    render(<Rail rows={all} folders={FF} selKey="" onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} onHome={() => {}}
      onMoveRows={() => {}} onFolderCreate={() => {}} />);
    const secs = [...document.querySelectorAll("[data-section]")].map((x) => x.getAttribute("data-section"));
    expect(secs).toEqual(["pinned", "recent", "f-launch", "ungrouped", "hidden"]);
    const recent = [...document.querySelectorAll('[data-section="recent"] [data-row-key]')].map((e) => e.getAttribute("data-row-key"));
    expect(recent).toHaveLength(8);
    expect(recent[0]).toBe("c:new");                                  // the last chat is the top row, even filed
    const launch = [...document.querySelectorAll('[data-section="f-launch"] [data-row-key]')].map((e) => e.getAttribute("data-row-key"));
    expect(launch).toEqual(expect.arrayContaining(["c:old", "c:new"]));
    expect(screen.getByRole("button", { name: "New folder" })).toBeTruthy();
  });
  it("with home2 off, the folder sidebar is unchanged (New folder, no room buttons)", () => {
    setFeatureOverride({ home2: false, rooms: false });
    render(<Rail rows={rows()} folders={F} selKey="" onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} onMove={() => {}} onFolderCreate={() => {}} />);
    expect(screen.getByRole("button", { name: "New folder" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New room" })).toBeNull();
  });
  it("the header is one + New menu: New chat, New room, New workspace; no bell, compass, # or collapse (home2)", () => {
    setFeatureOverride({ home2: true, rooms: true });
    const onHome = vi.fn(), onNewRoom = vi.fn(), onNewWorkspace = vi.fn();
    render(<Rail rows={rows()} folders={F} selKey="" onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} onHome={onHome}
      onNewRoom={onNewRoom} onNewWorkspace={onNewWorkspace} onBrowseRooms={() => {}} onCollapse={() => {}} />);
    const head = document.querySelector(".vx-rail-head") as HTMLElement;
    expect(within(head).getAllByRole("button")).toHaveLength(1);
    fireEvent.click(within(head).getByRole("button", { name: /New/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /New room/ }));
    expect(onNewRoom).toHaveBeenCalledTimes(1);
    fireEvent.click(within(head).getByRole("button", { name: /New/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /New chat/ }));
    expect(onHome).toHaveBeenCalledTimes(1);
    fireEvent.click(within(head).getByRole("button", { name: /New/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: /New workspace/ }));
    expect(onNewWorkspace).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Browse open rooms" })).toBeNull();
  });
  it("without onHome the title is plain text and there is no Home entry", () => {
    setFeatureOverride({ home2: false });
    render(<Rail rows={rows()} folders={F} selKey="" onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} />);
    expect(screen.queryByRole("button", { name: "Home" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Chats" })).toBeNull();
  });
});
