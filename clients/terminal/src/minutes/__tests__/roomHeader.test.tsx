/** R1 — the room header and the room dialogs, as approved (founder 2026-10-15: "this is mess" →
 *  essentials). The header names the room with # or a lock and one quiet line; there is no
 *  "Writes to" chip; Vexa's setting, Context, Think privately, Activity, settings and Leave live in
 *  "…". Rooms are made in a workspace, open by default; opening a closed room is confirmed. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../Participants", () => ({ Participants: () => <span data-testid="people">You, Ines</span> }));
vi.mock("../CallButton", () => ({ CallButton: () => <button type="button">Call</button> }));

import { RoomHeader, RoomSettingsDialog } from "../rooms/RoomHeader";
import { NewRoomDialog } from "../rooms/RoomDialogs";
import type { RoomSettings } from "../../surfaces/roomsApi";

const room = (over: Partial<RoomSettings> = {}): RoomSettings => ({ session_id: "s_1", room: true, humans: 3, visibility: "open", proactivity: "quiet", workspace: "w1", ...over });
let fetchMock: ReturnType<typeof vi.fn>;
const okJson = (body: unknown, status = 200) => ({ ok: true, status, json: async () => body }) as unknown as Response;

beforeEach(() => {
  fetchMock = vi.fn(async () => okJson(room({ proactivity: "active" })));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const noop = () => {};

describe("RoomHeader", () => {
  it("names the room, its workspace, how many are in it and Vexa's setting — and has no Writes-to chip", () => {
    render(<RoomHeader session="s_1" label="Launch plan" room={room()} workspaceName="Product planning" onLeave={noop} onThinkPrivately={noop} />);
    expect(screen.getByLabelText("Open room")).toBeTruthy();
    expect(screen.getByText("Launch plan")).toBeTruthy();
    expect(screen.getByText(/Product planning/).textContent).toContain("Product planning");
    expect(document.querySelector(".vx-rh-sub")!.textContent).toBe("Product planning · 3 members · Vexa speaks up when it matters");
    expect(screen.queryByText(/writes to/i)).toBeNull();
  });
  it("while the room's agent works on something nobody here asked for, the line says \"Vexa is checking…\"", () => {
    render(<RoomHeader session="s_1" label="Launch plan" room={room({ agent_busy: true })} workspaceName="Product planning" onLeave={noop} onThinkPrivately={noop} />);
    expect(screen.getByRole("status").textContent).toBe("Vexa is checking…");
  });
  it("a closed room shows a lock", () => {
    render(<RoomHeader session="s_1" label="Board" room={room({ visibility: "closed" })} workspaceName="Product planning" onLeave={noop} onThinkPrivately={noop} />);
    expect(screen.getByLabelText("Closed room")).toBeTruthy();
  });
  it("\"…\" holds Vexa's setting, Context, Think privately, Activity, Room settings and Leave; choosing a setting PATCHes the room", async () => {
    const think = vi.fn();
    render(<RoomHeader session="s_1" label="Launch plan" room={room()} workspaceName="Product planning" onLeave={noop} onThinkPrivately={think} onActivity={noop} />);
    fireEvent.click(screen.getByRole("button", { name: "More for this room" }));
    const items = () => [...screen.getAllByRole("menuitemradio"), ...screen.getAllByRole("menuitem")];
    const labels = items().map((m) => m.textContent);
    for (const want of ["Vexa answers when asked", "Vexa speaks up when it matters", "Vexa joins in", "Schedule a call…", "Context — what Vexa sees…", "Think privately", "Activity…", "Room settings…", "Leave room"])
      expect(labels.some((l) => l?.includes(want))).toBe(true);
    expect(screen.getAllByRole("menuitemradio").find((m) => m.textContent?.includes("Vexa speaks up when it matters"))!.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(items().find((m) => m.textContent?.includes("Vexa joins in"))!);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls.at(-1)! as [string, RequestInit];
    expect(url).toBe("/api/sessions/s_1/room");
    expect(JSON.parse(String(init.body))).toEqual({ proactivity: "active" });
  });
});

describe("RoomSettingsDialog", () => {
  it("opening a closed room says who will see its history, and only the second Save sends confirm_history", async () => {
    const close = vi.fn();
    render(<RoomSettingsDialog session="s_1" label="Board" room={room({ visibility: "closed" })} workspaceName="Product planning" onClose={close} />);
    fireEvent.click(screen.getByRole("radio", { name: /Open/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Everyone in Product planning will see this room and its history/)).toBeTruthy();
    const patches = () => fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PATCH");
    expect(patches()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Open to the workspace" }));
    await waitFor(() => expect(close).toHaveBeenCalled());
    expect(JSON.parse(String((fetchMock.mock.calls.at(-1)! as [string, RequestInit])[1].body))).toEqual({ visibility: "open", workspace: "w1", confirm_history: true });
  });
});

describe("RoomSettingsDialog — the room's model", () => {
  it("lists the catalog, and Save PATCHes {level} — one model for everybody in the room", async () => {
    fetchMock.mockImplementation(async (url: string) => String(url).startsWith("/api/models/catalog")
      ? okJson({ models: [{ id: "m-fast", display_name: "Fast", provider: "p", adapter: "openrouter", harness: "", capabilities: {}, access: "everyone", default: true }], default: "m-fast" })
      : okJson(room({ level: "m-fast" })));
    const close = vi.fn();
    render(<RoomSettingsDialog session="s_1" label="Board" room={room()} workspaceName="Product planning" onClose={close} />);
    const sel = await screen.findByRole("combobox");
    await screen.findByRole("option", { name: "Fast" });
    fireEvent.change(sel, { target: { value: "m-fast" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(close).toHaveBeenCalled());
    const patch = fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PATCH")! as [string, RequestInit];
    expect(patch[0]).toBe("/api/sessions/s_1/room");
    expect(JSON.parse(String(patch[1].body))).toEqual({ level: "m-fast" });
  });
});

describe("NewRoomDialog", () => {
  it("makes an OPEN room by default in the chosen workspace and hands back its session", async () => {
    fetchMock.mockResolvedValueOnce(okJson({ session_id: "s_9", name: "Launch", room: true, visibility: "open" }, 201));
    const made = vi.fn();
    render(<NewRoomDialog workspaces={[{ slug: "w1", name: "Product planning" }, { slug: "w2", name: "Growth" }]} onClose={noop} onCreated={made} />);
    expect(screen.getByText("Everyone in Product planning can see and join it.")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("e.g. Launch plan"), { target: { value: "Launch" } });
    fireEvent.click(screen.getByRole("button", { name: "Create room" }));
    await waitFor(() => expect(made).toHaveBeenCalledWith("s_9", "Launch", "w1"));
    expect(JSON.parse(String((fetchMock.mock.calls.at(-1)! as [string, RequestInit])[1].body))).toEqual({ workspace: "w1", name: "Launch", visibility: "open" });
  });
  it("Take to a room says the chat stays private, and is named after it", () => {
    render(<NewRoomDialog workspaces={[{ slug: "w1", name: "Product planning" }]} initialName="Pricing ideas" from={{ label: "Pricing ideas" }} onClose={noop} onCreated={noop} />);
    expect(screen.getByText(/Your chat stays private/)).toBeTruthy();
    expect((screen.getByPlaceholderText("e.g. Launch plan") as HTMLInputElement).value).toBe("Pricing ideas");
  });
  it("a refusal is said in the dialog, not swallowed", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: { refused: "not_a_workspace_member" } }) } as unknown as Response);
    render(<NewRoomDialog workspaces={[{ slug: "w1", name: "Product planning" }]} initialName="X" onClose={noop} onCreated={noop} />);
    fireEvent.click(screen.getByRole("button", { name: "Create room" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
