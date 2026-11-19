import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, act, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// Founder 2026-10-13/14: notifications "work but are not actionable" — an invite he could not accept
// from its notification. Each notice now offers its primary act in one tap (Accept → the workspace
// opens), marks itself read, and reaches a person whose tab is in the background.

const frameFns = new Set<(f: unknown) => void>();
vi.mock("../../surfaces/gatewayWS", async (orig) => {
  const real = await orig<typeof import("../../surfaces/gatewayWS")>();
  return { ...real, onUserFrame: (fn: (f: unknown) => void) => { frameFns.add(fn); return () => frameFns.delete(fn); },
    onGatewayWSConnected: () => () => {} };
});

import { parseUserFrame } from "../../surfaces/gatewayWS";
import { __resetNotifications, __setAlerts, getNotifications, handleFrame, primaryAction, relTime, sortForList } from "../notifications";
import { NotificationBell } from "../NotificationBell";
import { NotificationToasts } from "../NotificationToasts";
import { FOCUS_WORKSPACE_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../platform/events";

const GOLDEN = join(__dirname, "../../../../../core/gateway/contracts/ws.v1/golden");
const NOW = 1_760_360_000;
const notice = (id: string, extra: Record<string, unknown> = {}) => ({
  id, kind: "chat_added", text: `Alice added you to the chat “Q${id}”`, created: NOW, read: false,
  actor: { id: "u_alice", name: "Alice" }, object: { kind: "chat", id: `s-${id}`, name: `Q${id}` }, actions: ["open"], ...extra,
});
const invite = (id = "i1") => notice(id, { kind: "workspace_invited", text: "Alice invited you to the workspace “Acme”",
  object: { kind: "workspace", id: "acme", name: "Acme", url: "https://app.example.test/join?i=tok" }, actions: ["accept", "decline", "open"] });
const push = (n: unknown) => act(() => { frameFns.forEach((fn) => fn({ type: "notification", notification: n })); });

let calls: { url: string; body?: string }[] = [];
function fakeFetch(list: unknown[] = [], unread = 0, actAnswer?: (b: { id: string; action: string }) => unknown) {
  calls = [];
  return vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    if (String(url).startsWith("/api/notifications/act")) {
      const b = JSON.parse(String(init?.body));
      return new Response(JSON.stringify(actAnswer ? actAnswer(b) : {}), { status: 200 });
    }
    if (String(url).startsWith("/api/notifications/read")) return new Response(JSON.stringify({ marked: 1, unread: 0 }), { status: 200 });
    return new Response(JSON.stringify({ notifications: list, unread }), { status: 200 });
  }) as unknown as typeof fetch;
}
const accepted = (b: { id: string }) => ({
  notification: { ...invite(b.id), read: true, actions: ["open"], state: "accepted" },
  open: { kind: "workspace", id: "acme", name: "Acme" }, said: "Joined Acme.",
});

function listen(name: string) {
  const got: unknown[] = [];
  const on = (e: Event) => got.push((e as CustomEvent).detail);
  window.addEventListener(name, on);
  return { got, off: () => window.removeEventListener(name, on) };
}

beforeEach(() => { __resetNotifications(fakeFetch()); __setAlerts(null); try { localStorage.clear(); } catch { /* */ } });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("the frames", () => {
  it("parse the ws.v1 goldens with their actions, state, and the update frame", () => {
    const inv = parseUserFrame(JSON.parse(readFileSync(join(GOLDEN, "Notification.workspace-invited.json"), "utf8")));
    expect(inv?.type === "notification" && inv.notification.actions).toEqual(["accept", "decline", "open"]);
    const up = parseUserFrame(JSON.parse(readFileSync(join(GOLDEN, "NotificationUpdate.accepted.json"), "utf8")));
    expect(up).toMatchObject({ type: "notification.update", unread: 0, notification: { state: "accepted", actions: ["open"] } });
  });

  it("an update from another tab replaces the row and the count", () => {
    handleFrame({ type: "notification", notification: invite() } as never);
    handleFrame({ type: "notification.update", unread: 0, notification: { ...invite(), read: true, state: "accepted", actions: ["open"] } } as never);
    expect(getNotifications().items[0]).toMatchObject({ state: "accepted", read: true });
    expect(getNotifications().unread).toBe(0);
  });
});

describe("one tap", () => {
  it("names each kind's primary act", () => {
    expect(primaryAction(invite() as never)).toEqual({ action: "accept", label: "Accept" });
    expect(primaryAction({ ...invite(), state: "accepted", actions: ["open"] } as never)).toEqual({ action: "open", label: "Open" });
    expect(primaryAction(notice("m", { kind: "meeting_shared", object: { kind: "meeting", id: "42" } }) as never)?.label).toBe("Open transcript");
    expect(primaryAction(notice("c", { kind: "chat_mentioned" }) as never)?.label).toBe("Open chat");
    expect(primaryAction(notice("b", { kind: "bot_joined", object: { kind: "meeting", id: "43" } }) as never)?.label).toBe("Open");
    expect(primaryAction(notice("s", { kind: "bot_scheduled", object: { kind: "none", id: "cal" }, actions: [] }) as never)).toBeNull();
  });

  it("Accept in the toast joins, opens the workspace and marks the notice read", async () => {
    __resetNotifications(fakeFetch([], 0, accepted));
    render(<NotificationToasts />);
    push(invite());
    const focus = listen(FOCUS_WORKSPACE_EVENT);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Accept" })); });
    focus.off();
    expect(JSON.parse(calls.find((c) => c.url === "/api/notifications/act")!.body!)).toEqual({ id: "i1", action: "accept" });
    expect(focus.got).toEqual([{ workspace: "acme", name: "Acme" }]);
    expect(getNotifications().items[0]).toMatchObject({ state: "accepted", read: true });
    expect(getNotifications().unread).toBe(0);
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  it("Decline in the list answers the invite and leaves no act behind", async () => {
    __resetNotifications(fakeFetch([invite()], 1, (b) => ({ notification: { ...invite(b.id), read: true, actions: [], state: "declined" }, open: null })));
    await act(async () => { render(<NotificationBell />); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Notifications/ })); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Decline" })); });
    expect(JSON.parse(calls.find((c) => c.url === "/api/notifications/act")!.body!)).toEqual({ id: "i1", action: "decline" });
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(screen.getByText("Declined")).toBeTruthy();
  });

  it("an accepted invite reads Joined · Open, and Open opens the workspace", async () => {
    __resetNotifications(fakeFetch([{ ...invite(), read: true, state: "accepted", actions: ["open"] }], 0));
    await act(async () => { render(<NotificationBell />); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Notifications/ })); });
    expect(screen.getByText("Joined")).toBeTruthy();
    const focus = listen(FOCUS_WORKSPACE_EVENT);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Open" })); });
    focus.off();
    expect(focus.got).toEqual([{ workspace: "acme", name: "Acme" }]);
  });

  it("Open transcript on a shared meeting goes to its link, or opens the meeting by id", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<NotificationToasts />);
    push(notice("m1", { kind: "meeting_shared", text: "Alice shared the meeting “Weekly” with you",
      object: { kind: "meeting", id: "42", name: "Weekly", url: "/?tshare=abc" } }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Open transcript" })); });
    expect(assign).toHaveBeenCalledWith("/?tshare=abc");
    vi.unstubAllGlobals();
    push(notice("b1", { kind: "bot_joined", text: "Vexa joined “Weekly”", object: { kind: "meeting", id: "43", name: "Weekly" } }));
    const meet = listen(OPEN_MEETING_EVENT);
    await act(async () => { fireEvent.click(within(screen.getByText(/Vexa joined/).closest(".vx-notice-toast") as HTMLElement).getByRole("button", { name: "Open" })); });
    meet.off();
    expect(meet.got).toEqual([{ ref: "43" }]);
  });

  it("Open chat on a mention opens the chat", async () => {
    render(<NotificationToasts />);
    push(notice("c1", { kind: "chat_mentioned", text: "Alice mentioned you in “Q”" }));
    const open = listen(OPEN_SESSION_EVENT);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Open chat" })); });
    open.off();
    expect(open.got).toEqual([{ session: "s-c1" }]);
    expect(getNotifications().unread).toBe(0);
  });
});

describe("the list", () => {
  it("shows unread first, grouped New / Earlier, with relative times", async () => {
    const t = Date.now() / 1000;
    __resetNotifications(fakeFetch([
      notice("old", { read: true, created: t - 3600 }), notice("new1", { created: t - 300 }), notice("new2", { created: t - 30 })], 2));
    await act(async () => { render(<NotificationBell />); });
    const sorted = sortForList(getNotifications().items).map((n) => n.id);
    expect(sorted).toEqual(["new2", "new1", "old"]);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Notifications/ })); });
    const list = screen.getByRole("dialog", { name: "Notifications" });
    const heads = within(list).getAllByRole("heading").map((h) => h.textContent);
    expect(heads).toEqual(["New", "Earlier"]);
    expect(within(list).getByText("5 min ago")).toBeTruthy();
    expect(within(list).getByText("1 h ago")).toBeTruthy();
  });

  it("relative times", () => {
    const now = 1_760_360_000_000;
    expect(relTime(now - 20_000, now)).toBe("just now");
    expect(relTime(now - 3 * 60_000, now)).toBe("3 min ago");
    expect(relTime(now - 2 * 3600_000, now)).toBe("2 h ago");
    expect(relTime(now - 30 * 3600_000, now)).toBe("yesterday");
  });

  it("is calm when empty", async () => {
    await act(async () => { render(<NotificationBell />); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Notifications/ })); });
    expect(screen.getByText("You're all caught up")).toBeTruthy();
  });
});

describe("a tab in the background", () => {
  it("raises a browser notification and a soft sound only while the tab is hidden", () => {
    const shown: string[] = [];
    let chimes = 0;
    __setAlerts({ hidden: () => true, permission: () => "granted", show: (n) => { shown.push(n.text); }, chime: () => { chimes += 1; } });
    handleFrame({ type: "notification", notification: notice("1") } as never);
    expect(shown).toEqual(["Alice added you to the chat “Q1”"]);
    expect(chimes).toBe(1);
    __setAlerts({ hidden: () => false, permission: () => "granted", show: (n) => { shown.push(n.text); }, chime: () => { chimes += 1; } });
    handleFrame({ type: "notification", notification: notice("2") } as never);
    expect(shown).toHaveLength(1);
    expect(chimes).toBe(1);
  });

  it("asks for permission once, politely, from the bell", async () => {
    const ask = vi.fn(async () => "granted" as NotificationPermission);
    __setAlerts({ hidden: () => false, permission: () => "default", request: ask, show: () => {}, chime: () => {} });
    await act(async () => { render(<NotificationBell />); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Notifications/ })); });
    expect(screen.getByText(/when this tab is in the background/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Turn on" })); });
    expect(ask).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/when this tab is in the background/)).toBeNull();
    cleanup();
    await act(async () => { render(<NotificationBell />); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Notifications/ })); });
    expect(screen.queryByText(/when this tab is in the background/)).toBeNull();
  });
});
