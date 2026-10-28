/** UI-1 (0.13.4, feature `shell2`): the one-list sidebar — + New · Inbox · Meetings, no "Chats"
 *  header, the All · Rooms · Routines filter with counts, routines among the chats (no Routines
 *  group), no meetings in the chat list; Pinned → Recent → folders → Hidden. Off: unchanged. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Rail } from "../../Rail";
import { applyFolders, type RailFolders } from "../../railFolders";
import { setFeatureOverride } from "../../../platform/features";
import { centreNow, resetCentre } from "../centre";
vi.mock("../../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

const F: RailFolders = { served: true, order: {}, filings: { "c:filed": "f-a" },
  folders: [{ id: "pinned", name: "Pinned", builtin: true, collapsed: false }, { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
    { id: "routines", name: "Routines", builtin: true, collapsed: false },
    { id: "f-a", name: "Folder A", builtin: false, collapsed: false }, { id: "hidden", name: "Hidden", builtin: true, collapsed: true }] };
const row = (key: string, extra: Record<string, unknown> = {}) => ({ key, chatId: key.slice(2), label: `Row ${key}`, whenLabel: "1:00 PM", when: 1, ...extra }) as never;
const rows = () => applyFolders([row("c:chat"), row("c:room", { shared: true }), row("c:rt", { routine: { flow: "digest" } }),
  row("c:filed"), row("m:call", { kind: "call", meetingId: "9", chatId: null }), row("c:pin", { pinned: true })], F);
const rail = (onHome = vi.fn()) => render(<Rail rows={rows()} folders={F} selKey="" onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}}
  onHome={onHome} onMoveRows={() => {}} />);

afterEach(() => { cleanup(); resetCentre(); setFeatureOverride("all"); try { localStorage.removeItem("vexa.rail.filter"); } catch { /* */ } });

describe("the one-list sidebar (shell2)", () => {
  it("starts with + New · Inbox · Meetings · People and has no Chats header", () => {
    setFeatureOverride({ home2: true, rooms: true, shell2: true });
    const onHome = vi.fn();
    rail(onHome);
    expect(document.querySelector(".vx-rail-head")).toBeNull();
    expect([...document.querySelectorAll("[data-s2-nav]")].map((e) => e.getAttribute("data-s2-nav"))).toEqual(["new", "inbox", "meetings", "people"]);
    fireEvent.click(screen.getByRole("button", { name: /^New$/ }));
    expect(onHome).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /^Meetings/ }));
    expect(centreNow()).toBe("meetings");
  });
  it("lists chats, rooms and routines but no meetings; routines sit among the chats, not in a group", () => {
    setFeatureOverride({ home2: true, rooms: true, shell2: true });
    rail();
    const keys = [...document.querySelectorAll("[data-row-key]")].map((e) => e.getAttribute("data-row-key"));
    expect(keys).not.toContain("m:call");
    expect(keys).toContain("c:rt");
    expect(document.querySelector('[data-section="routines"]')).toBeNull();
    const secs = [...document.querySelectorAll("[data-section]")].map((x) => x.getAttribute("data-section"));
    expect(secs.indexOf("pinned")).toBeLessThan(secs.indexOf("recent"));
    expect(secs.indexOf("recent")).toBeLessThan(secs.indexOf("f-a"));
    expect(secs[secs.length - 1]).toBe("hidden");
  });
  it("filters All · Rooms · Routines, with counts, and remembers the choice", () => {
    setFeatureOverride({ home2: true, rooms: true, shell2: true });
    rail();
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["All5", "Rooms1", "Routines1"]);
    fireEvent.click(screen.getByRole("tab", { name: /Routines/ }));
    const keys = new Set([...document.querySelectorAll("[data-row-key]")].map((e) => e.getAttribute("data-row-key")));
    expect([...keys]).toEqual(["c:rt"]);
    expect(localStorage.getItem("vexa.rail.filter")).toBe("routines");
  });
  it("with shell2 off, the home2 sidebar is unchanged (the Chats header and its + New menu)", () => {
    setFeatureOverride({ home2: true, rooms: true, shell2: false });
    rail();
    expect(document.querySelector(".vx-rail-head")).toBeTruthy();
    expect(document.querySelector("[data-shell2-nav]")).toBeNull();
  });
});
