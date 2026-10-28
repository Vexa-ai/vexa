/** Isolation harness — the rooms surface's data access (R1, rooms flag). Scoped (no subject, P20),
 *  fail-loud (P18), and the agent-api contract of `routers/rooms.py`: POST /api/rooms, GET /api/rooms,
 *  POST /api/rooms/{s}/join, GET/PATCH /api/sessions/{s}/room. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createRoom, joinRoom, leaveRoom, listRooms, openRoomToWorkspace, planRoomCall, readRoom, readRoomMembers, setRoomProactivity, setRoomVisibility } from "../roomsApi";
import { ApiError } from "../../data/apiClient";

let fetchMock: ReturnType<typeof vi.fn>;
const last = () => fetchMock.mock.calls.at(-1)! as [string, RequestInit | undefined];
const ok = (body: unknown, status = 200) => ({ ok: true, status, json: async () => body }) as unknown as Response;

beforeEach(() => {
  fetchMock = vi.fn(async () => ok({ session_id: "s_1", room: true, humans: 2, visibility: "open", proactivity: "quiet", workspace: "w1", listen: {}, listen_version: 0 }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());

describe("roomsApi", () => {
  it("createRoom POSTs {workspace, name, visibility, members} to /api/rooms, no subject", async () => {
    fetchMock.mockResolvedValueOnce(ok({ session_id: "s_9", name: "Launch", room: true, visibility: "open" }, 201));
    const r = await createRoom({ workspace: "w1", name: "Launch", visibility: "open", members: ["u2"] });
    expect(r.session_id).toBe("s_9");
    const [url, init] = last();
    expect(url).toBe("/api/rooms");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ workspace: "w1", name: "Launch", visibility: "open", members: ["u2"] });
    expect(url).not.toContain("subject");
  });
  it("createRoom leaves members out when there are none (the body is extra=forbid on the server)", async () => {
    await createRoom({ workspace: "w1", name: "Launch", visibility: "closed" });
    expect(JSON.parse(String(last()[1]?.body))).toEqual({ workspace: "w1", name: "Launch", visibility: "closed" });
  });
  it("listRooms GETs /api/rooms?workspace= (encoded) and answers its rooms", async () => {
    fetchMock.mockResolvedValueOnce(ok({ workspace: "w 1", rooms: [{ id: "s_1", title: "Launch", visibility: "open", member: false }] }));
    const rooms = await listRooms("w 1");
    expect(last()[0]).toBe("/api/rooms?workspace=w%201");
    expect(rooms[0].title).toBe("Launch");
  });
  it("joinRoom POSTs /api/rooms/{s}/join", async () => {
    await joinRoom("s_1");
    expect(last()[0]).toBe("/api/rooms/s_1/join");
    expect(last()[1]?.method).toBe("POST");
  });
  it("leaveRoom POSTs /api/rooms/{s}/leave", async () => {
    await leaveRoom("s_1");
    expect(last()[0]).toBe("/api/rooms/s_1/leave");
    expect(last()[1]?.method).toBe("POST");
  });
  it("readRoom GETs /api/sessions/{s}/room; a private chat reads room:false", async () => {
    fetchMock.mockResolvedValueOnce(ok({ session_id: "s_2", room: false, humans: 1, visibility: "private", proactivity: "quiet", workspace: "", listen: {}, listen_version: 0 }));
    const r = await readRoom("s_2");
    expect(last()[0]).toBe("/api/sessions/s_2/room");
    expect(r.room).toBe(false);
  });
  it("setRoomProactivity / setRoomVisibility PATCH only the field they change", async () => {
    await setRoomProactivity("s_1", "active");
    expect(last()[1]?.method).toBe("PATCH");
    expect(JSON.parse(String(last()[1]?.body))).toEqual({ proactivity: "active" });
    await setRoomVisibility("s_1", "closed");
    expect(JSON.parse(String(last()[1]?.body))).toEqual({ visibility: "closed" });
  });
  it("opening a room to its workspace says the history is seen (confirm_history) — only when the person confirmed", async () => {
    await openRoomToWorkspace("s_1", { workspace: "w1", confirmed: true });
    expect(JSON.parse(String(last()[1]?.body))).toEqual({ visibility: "open", workspace: "w1", confirm_history: true });
  });
  it("planRoomCall POSTs /api/rooms/{s}/plan with only the fields given (extra=forbid on the server)", async () => {
    await planRoomCall("s_1", { title: "Sync", start: "2026-10-20T13:00:00.000Z" });
    expect(last()[0]).toBe("/api/rooms/s_1/plan");
    expect(last()[1]?.method).toBe("POST");
    expect(JSON.parse(String(last()[1]?.body))).toEqual({ title: "Sync", start: "2026-10-20T13:00:00.000Z" });
    await planRoomCall("s_1", { title: "Sync", start: "a", end: "b", invitees: ["x@y.io"], agenda: "p.md" });
    expect(JSON.parse(String(last()[1]?.body))).toEqual({ title: "Sync", start: "a", end: "b", invitees: ["x@y.io"], agenda: "p.md" });
  });
  it("readRoomMembers GETs /api/rooms/{s}/members and answers its members", async () => {
    fetchMock.mockResolvedValueOnce(ok({ session_id: "s_1", members: [{ principal: "7", kind: "person", role: "owner" }] }));
    expect((await readRoomMembers("s_1")).map((m) => m.principal)).toEqual(["7"]);
    expect(last()[0]).toBe("/api/rooms/s_1/members");
  });
  it("FAIL-LOUD: a refusal throws ApiError (the dialog says why)", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: { refused: "not_a_workspace_member" } }) } as unknown as Response);
    await expect(createRoom({ workspace: "w1", name: "x", visibility: "open" })).rejects.toBeInstanceOf(ApiError);
  });
});
