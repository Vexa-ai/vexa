/** S4 rooms of two in the shell2 sidebar and the person panel's Message (feature-detected). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Rail } from "../../Rail";
import { applyFolders, type RailFolders } from "../../railFolders";
import { setFeatureOverride } from "../../../platform/features";
import { rememberRoom, resetRoomState } from "../../../surfaces/roomState";
import { resetPairPeers } from "../pairRooms";
import { resetRoomsOfTwo, roomsOfTwoAvailable } from "../messageRoom";
vi.mock("../../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

const F: RailFolders = { served: true, order: {}, filings: {},
  folders: [{ id: "pinned", name: "Pinned", builtin: true, collapsed: false }, { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
    { id: "hidden", name: "Hidden", builtin: true, collapsed: true }] };
const row = (key: string, extra: Record<string, unknown> = {}) => ({ key, chatId: key.slice(2), label: `Row ${key}`, whenLabel: "", when: 1, ...extra }) as never;
const room = (sid: string, visibility: "open" | "direct", workspace = "w1") =>
  rememberRoom(sid, { session_id: sid, room: true, humans: 2, visibility, proactivity: "quiet", workspace });

afterEach(() => { cleanup(); resetRoomState(); resetPairPeers(); resetRoomsOfTwo(); vi.unstubAllGlobals(); setFeatureOverride("all"); try { localStorage.removeItem("vexa.rail.filter"); } catch { /* */ } });

describe("rooms of two in the sidebar (S4)", () => {
  it("under Rooms, sit together under People, named by the other person (from the chat list row)", async () => {
    setFeatureOverride({ home2: true, rooms: true, shell2: true });
    const f = vi.fn(async () => new Response("{}", { status: 404 }));
    vi.stubGlobal("fetch", f);
    room("team", "open");
    render(<Rail rows={applyFolders([row("c:pair1", { shared: true, visibility: "direct", pairPeer: { principal: "u_maya", name: "Maya Hart" }, unread: 2 }),
      row("c:team", { shared: true }), row("c:solo")], F)} folders={F} selKey=""
      onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} onHome={() => {}} onMoveRows={() => {}} />);
    fireEvent.click(screen.getByRole("tab", { name: /Rooms/ }));
    const secs = [...document.querySelectorAll("[data-section]")].map((x) => x.getAttribute("data-section"));
    expect(secs[0]).toBe("people-rooms");
    expect([...document.querySelectorAll('[data-section="people-rooms"] [data-row-key]')].map((e) => e.getAttribute("data-row-key"))).toEqual(["c:pair1"]);
    await waitFor(() => expect(document.querySelector('[data-row-key="c:pair1"] [data-pair-room]')?.textContent).toBe("Maya Hart"));
    expect(f.mock.calls.some((c) => String((c as unknown[])[0]).includes("/people"))).toBe(false);   // no members read per room
  });
});

describe("Message (the room of two) follows the rooms feature", () => {
  it("is offered when the server answers rooms on, and never probes the route", () => {
    const f = vi.fn(async () => new Response("{}", { status: 422 }));
    vi.stubGlobal("fetch", f);
    setFeatureOverride({ home2: true, rooms: true, shell2: true });
    expect(roomsOfTwoAvailable()).toBe(true);
    expect(f).not.toHaveBeenCalled();
  });
  it("stays off with rooms off", () => {
    setFeatureOverride({ home2: true, rooms: false, shell2: true });
    expect(roomsOfTwoAvailable()).toBe(false);
  });
});
