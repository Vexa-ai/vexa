import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, act, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// The socket is the module under test's input, not a network: frames are handed in through it.
const frameFns = new Set<(f: unknown) => void>();
vi.mock("../../surfaces/gatewayWS", async (orig) => {
  const real = await orig<typeof import("../../surfaces/gatewayWS")>();
  return { ...real, onUserFrame: (fn: (f: unknown) => void) => { frameFns.add(fn); return () => frameFns.delete(fn); },
    onGatewayWSConnected: () => () => {} };
});
vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

import { parseUserFrame } from "../../surfaces/gatewayWS";
import { __resetNotifications, getNotifications, handleFrame, handleRailFrame, markRead, RAIL_CHANGED_EVENT, TOAST_MAX, TOAST_MS } from "../notifications";
import { NotificationBell } from "../NotificationBell";
import { NotificationToasts } from "../NotificationToasts";
import { Rail } from "../Rail";
import { applyFolders, folderOf, type RailFolders } from "../railFolders";
import { chatsFromSessions, dropLeftShared, initials, railRows } from "../chats";
import { OPEN_SESSION_EVENT } from "../../platform/events";

// Founder 2026-10-13: "we need notifications popup in UI when people invite each other etc, and if a
// chat session is shared it should appear in their chats as shared".

const GOLDEN = join(__dirname, "../../../../../core/gateway/contracts/ws.v1/golden");
const notice = (id: string, extra: Record<string, unknown> = {}) => ({
  id, kind: "chat_added", text: `Alice added you to the chat “Q${id}”`, created: 1760360000, read: false,
  actor: { id: "u_alice", name: "Alice" }, object: { kind: "chat", id: `s-${id}`, name: `Q${id}` }, ...extra,
});
const push = (n: unknown) => act(() => { frameFns.forEach((fn) => fn({ type: "notification", notification: n })); });

let calls: { url: string; body?: string }[] = [];
function fakeFetch(list: unknown[] = [], unread = 0) {
  calls = [];
  return vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    if (String(url).startsWith("/api/notifications/read")) return new Response(JSON.stringify({ marked: 1, unread: 0 }), { status: 200 });
    return new Response(JSON.stringify({ notifications: list, unread }), { status: 200 });
  }) as unknown as typeof fetch;
}

beforeEach(() => { __resetNotifications(fakeFetch()); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("the frames on the person's own channel", () => {
  it("parses the ws.v1 goldens: a notification, a read count, a rail change", () => {
    for (const f of ["Notification.chat-shared.json", "NotificationRead.all.json", "Rail.added.json"]) {
      const data = JSON.parse(readFileSync(join(GOLDEN, f), "utf8"));
      expect(parseUserFrame(data)?.type).toBe(data.type);
    }
    expect(parseUserFrame({ type: "meeting.status", status: "active" })).toBeNull();
    expect(parseUserFrame({ type: "notification" })).toBeNull();
  });

  it("a rail frame tells the shell to read its chats again", () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(RAIL_CHANGED_EVENT, on);
    handleRailFrame({ type: "rail", change: "added", session: "s-1" });
    handleFrame({ type: "rail", change: "added", session: "s-2" });   // not the notifications path
    window.removeEventListener(RAIL_CHANGED_EVENT, on);
    expect(seen).toEqual([{ change: "added", session: "s-1" }]);
  });

  it("rail frames are read with notifications never started (the feature switched off)", async () => {
    const ws = await import("../../surfaces/gatewayWS");
    const spy = vi.spyOn(ws, "onUserFrame");
    const { startRailFrames } = await import("../notifications");
    const stop = startRailFrames();
    expect(spy).toHaveBeenCalled();
    const handler = spy.mock.calls[spy.mock.calls.length - 1][0] as (f: unknown) => void;
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(RAIL_CHANGED_EVENT, on);
    handler({ type: "rail", change: "removed", session: "s-9" });
    window.removeEventListener(RAIL_CHANGED_EVENT, on);
    stop();
    spy.mockRestore();
    expect(seen).toEqual([{ change: "removed", session: "s-9" }]);
  });
});

describe("toasts", () => {
  it("a live notification shows top-right for six seconds, at most three stacked, newest first", () => {
    vi.useFakeTimers();
    render(<NotificationToasts />);
    for (const id of ["1", "2", "3", "4"]) push(notice(id));
    const shown = screen.getAllByRole("button", { name: /added you/ });
    expect(shown).toHaveLength(TOAST_MAX);
    expect(shown[0].textContent).toContain("Q4");
    expect(screen.queryByText(/Q1/)).toBeNull();
    act(() => { vi.advanceTimersByTime(TOAST_MS + 10); });
    expect(screen.queryAllByRole("button", { name: /added you/ })).toHaveLength(0);
    expect(getNotifications().unread).toBe(4);
  });

  it("clicking a toast opens its chat and marks it read", async () => {
    render(<NotificationToasts />);
    push(notice("7"));
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_SESSION_EVENT, on);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Q7/ })); });
    window.removeEventListener(OPEN_SESSION_EVENT, on);
    expect(opened).toEqual([{ session: "s-7" }]);
    expect(getNotifications().unread).toBe(0);
    expect(calls.some((c) => c.url === "/api/notifications/read" && JSON.parse(c.body!).ids[0] === "7")).toBe(true);
    expect(screen.queryByRole("button", { name: /Q7/ })).toBeNull();
  });

  it("a refreshed row (same id) is counted once", () => {
    handleFrame({ type: "notification", notification: notice("9") });
    handleFrame({ type: "notification", notification: notice("9") });
    expect(getNotifications().unread).toBe(1);
    expect(getNotifications().items).toHaveLength(1);
  });
});

describe("the bell", () => {
  it("loads the unread on mount, shows the count, opens the list and marks what it shows read", async () => {
    __resetNotifications(fakeFetch([notice("1"), notice("2", { read: true })], 1));
    await act(async () => { render(<NotificationBell />); });
    const bell = screen.getByRole("button", { name: "Notifications, 1 unread" });
    expect(bell.querySelector("[data-notice-count]")?.textContent).toBe("1");
    await act(async () => { fireEvent.click(bell); });
    const list = screen.getByRole("dialog", { name: "Notifications" });
    expect(within(list).getAllByRole("button", { name: /added you/ })).toHaveLength(2);
    expect(calls.some((c) => c.url === "/api/notifications/read" && JSON.parse(c.body!).ids.join() === "1")).toBe(true);
    expect(getNotifications().unread).toBe(0);
  });

  it("the list opens on the page, outside the rail that would clip it; a row opens its chat; the bell closes it again", async () => {
    // founder, live: the bell said "1" and "I can't open it in any way" — the rail is a size
    // container, the containing block of a fixed child, so the list opened clipped inside it
    __resetNotifications(fakeFetch([notice("1")], 1));
    const rail = document.createElement("nav");
    rail.className = "vx-rail";
    document.body.appendChild(rail);
    await act(async () => { render(<NotificationBell />, { container: rail }); });
    const bell = screen.getByRole("button", { name: "Notifications, 1 unread" });
    await act(async () => { fireEvent.click(bell); });
    const list = screen.getByRole("dialog", { name: "Notifications" });
    expect(rail.contains(list)).toBe(false);
    expect(list.closest(".vx-notice-portal")?.parentElement).toBe(document.body);
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_SESSION_EVENT, on);
    await act(async () => { fireEvent.click(within(list).getByRole("button", { name: /added you/ })); });
    window.removeEventListener(OPEN_SESSION_EVENT, on);
    expect(opened).toEqual([expect.objectContaining({ session: "s-1" })]);
    expect(screen.queryByRole("dialog", { name: "Notifications" })).toBeNull();
    await act(async () => { fireEvent.click(bell); });
    expect(screen.getByRole("dialog", { name: "Notifications" })).toBeTruthy();
    await act(async () => { fireEvent.pointerDown(bell); fireEvent.click(bell); });   // the bell closes it
    expect(screen.queryByRole("dialog", { name: "Notifications" })).toBeNull();
    rail.remove();
  });

  it("mark all read clears the count", async () => {
    __resetNotifications(fakeFetch([notice("1"), notice("2")], 2));
    await act(async () => { render(<NotificationBell />); });
    await act(async () => { await markRead("all"); });
    expect(getNotifications().unread).toBe(0);
    expect(JSON.parse(calls.at(-1)!.body!)).toEqual({ all: true });
  });

  it("a read count from another tab updates this one", () => {
    handleFrame({ type: "notification", notification: notice("1") });
    expect(getNotifications().unread).toBe(1);
    handleFrame({ type: "notification.read", unread: 0 });
    expect(getNotifications().unread).toBe(0);
    expect(getNotifications().items.every((n) => n.read)).toBe(true);
  });
});

describe("a shared chat in the recipient's rail", () => {
  const F: RailFolders = {
    served: true, filings: {}, order: {},
    folders: [
      { id: "pinned", name: "Pinned", builtin: true, collapsed: false },
      { id: "shared", name: "Shared with me", builtin: true, collapsed: false },
      { id: "f-aaaaaaaaaaaa", name: "Board", builtin: false, collapsed: false },
      { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
      { id: "hidden", name: "Hidden", builtin: true, collapsed: true },
    ],
  };
  const chats = chatsFromSessions([
    { session: "s-abc", title: "Board prep", shared: true, participants: 2, role: "member", owner: "u_alice", owner_name: "Alice Smith" },
    { session: "pchat-1", title: "Mine with Joe", shared: true, participants: 2, role: "owner" },
    { session: "pchat-2", title: "Private" },
  ]);

  it("somebody else's chat files in Shared with me by default and carries their initials; my shared chat a plain chip", () => {
    const rows = applyFolders(railRows(chats, []), F);
    const by = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(by["c:s-abc"]).toMatchObject({ folder: "shared", shared: true, sharedBy: "Alice Smith" });
    expect(by["c:pchat-1"]).toMatchObject({ folder: "ungrouped", shared: true });
    expect(by["c:pchat-1"].sharedBy).toBeUndefined();
    expect(by["c:pchat-2"].shared).toBeUndefined();
    // filed elsewhere by the person, it stays there
    expect(folderOf(by["c:s-abc"], { ...F, filings: { "c:s-abc": "f-aaaaaaaaaaaa" } })).toBe("f-aaaaaaaaaaaa");
    render(<Rail rows={rows} folders={F} selKey={null} onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} onMoveRows={() => {}} />);
    const shared = screen.getByRole("region", { name: "Shared with me" });
    // one icon, no "shared" text chip (founder 2026-10-14): their initials, and whose it is on hover
    const icon = within(shared).getByRole("img", { name: "Shared with you by Alice Smith" });
    expect(icon.getAttribute("data-row-icon")).toBe("shared");
    expect(icon.textContent).toContain("AS");
    expect(icon.closest("[data-row-shared]")!.getAttribute("data-row-shared")).toBe("from");
    const mine = screen.getByRole("region", { name: "Recent" });
    expect(within(mine).getByRole("img", { name: "You shared this chat" }).closest("[data-row-shared]")!.getAttribute("data-row-shared")).toBe("mine");
  });

  it("an empty Shared with me is not drawn", () => {
    const rows = applyFolders(railRows(chats.filter((c) => !c.owner), []), F);
    render(<Rail rows={rows} folders={F} selKey={null} onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} onMoveRows={() => {}} />);
    expect(screen.queryByRole("region", { name: "Shared with me" })).toBeNull();
  });

  it("leaving or being removed takes it off the rail; my own chats are never dropped this way", () => {
    const server = chats.filter((c) => c.id !== "s-abc" && c.id !== "pchat-2");
    expect(dropLeftShared(chats, server).map((c) => c.id)).toEqual(["pchat-1", "pchat-2"]);
  });

  it("initials", () => {
    expect(initials("Alice Smith")).toBe("AS");
    expect(initials("bob@acme.test")).toBe("B");
    expect(initials("")).toBe("");
  });
});

describe("an invite in the list", () => {
  it("an older server's invite row (no actions) still offers Accept and Decline, through the invite's own doors", async () => {
    const inv = notice("5", { kind: "workspace_invited", text: "Alice invited you to the workspace “Acme”",
      object: { kind: "workspace", id: "acme", name: "Acme", url: "https://app.example.test/join?i=tok-123" } });
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string | undefined });
      if (url === "/api/notifications/act") return new Response("{}", { status: 404 });
      if (url.startsWith("/api/notifications/read") || url.startsWith("/api/workspace")) return new Response(JSON.stringify({ unread: 0 }), { status: 200 });
      return new Response(JSON.stringify({ notifications: [inv], unread: 1 }), { status: 200 });
    }) as unknown as typeof fetch;
    __resetNotifications(f);
    calls = [];
    await act(async () => { render(<NotificationBell />); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Notifications/ })); });
    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Decline" })); });
    const decline = calls.find((c) => c.url === "/api/workspace/invites/decline");
    expect(JSON.parse(decline!.body!)).toEqual({ token: "tok-123" });
    expect(screen.queryByRole("button", { name: "Decline" })).toBeNull();
  });
});

describe("an agent's own notice (notify)", () => {
  it("renders like any other: a toast and the bell, its click opens the chat it came from", async () => {
    render(<NotificationToasts />);
    push(notice("a1", { kind: "agent", text: "3 emails need a reply today: the bank, the landlord, the school",
      actor: { id: "agent:u_alice", name: "check-emails" }, object: { kind: "chat", id: "s-r1", name: "Inbox digest" } }));
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_SESSION_EVENT, on);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /3 emails need a reply/ })); });
    window.removeEventListener(OPEN_SESSION_EVENT, on);
    expect(opened).toEqual([{ session: "s-r1" }]);
  });
});
