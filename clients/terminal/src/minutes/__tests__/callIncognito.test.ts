/** A call in an incognito chat starts NO transcriber (founder 2026-10-15): the dock asks meeting-api
 *  for an untranscribed room, mints no transcript share, and binds the call by its room alone. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { enterCall, leaveCall } from "../callState";
import { startCall, type CallJoin } from "../../surfaces/callsApi";

const CALL = { room: "call-quiet1", meeting_id: null, owner: "2", url: "wss://calls.test", started: 1, transcribed: false, participants: 0 };
const join: CallJoin = { session_id: "s-1", url: "wss://calls.test", token: "tok", role: "owner", call: CALL };

afterEach(() => { leaveCall(); vi.unstubAllGlobals(); });

describe("an incognito chat's call", () => {
  it("is started untranscribed; any other chat's call keeps the default", async () => {
    const startCallDep = vi.fn(async () => ({ session_id: "s-1", calls: true, call: CALL }));
    const deps = { getCall: vi.fn(async () => ({ session_id: "s-1", calls: true, call: null })), startCall: startCallDep,
      joinCall: vi.fn(async () => join) };
    await enterCall("pchat-1", "Private", { ...deps, incognito: async () => true } as never);
    expect(startCallDep).toHaveBeenLastCalledWith("pchat-1", "s-1", { transcribe: false });
    await enterCall("pchat-2", "Team", { ...deps, incognito: async () => false } as never);
    expect(startCallDep).toHaveBeenLastCalledWith("pchat-2", "s-1", {});
  });

  it("asks for no transcriber, mints no share, and binds by the room alone", async () => {
    const seen: { url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      seen.push({ url: String(url), body });
      if (String(url) === "/api/bots") return new Response(JSON.stringify({ id: null, native_meeting_id: "call-quiet1", transcribed: false }), { status: 201 });
      return new Response(JSON.stringify({ session_id: "s-1", calls: true, call: CALL }), { status: 200 });
    }));
    await startCall("pchat-1", "s-1", { transcribe: false });
    expect(seen[0]).toEqual({ url: "/api/bots", body: { platform: "vexa_call", session_id: "s-1", transcribe: false } });
    expect(seen.some((x) => x.url.includes("/share"))).toBe(false);
    expect(seen.at(-1)).toEqual({ url: "/api/sessions/pchat-1/call", body: { room: "call-quiet1" } });
  });
});
