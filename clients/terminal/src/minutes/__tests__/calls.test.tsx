/** A call in a session (ADR-0048 decision 6): joining starts the call only when the session has none,
 *  the Call button appears only where calls are deployed, the transcription notice is read from the
 *  room, and the owner's review is offered to the owner only. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { enterCall, leaveCall, activeCall, type CallDeps } from "../callState";
import { transcribedNotice } from "../CallRoom";
import type { CallJoin, CallState } from "../../surfaces/callsApi";

const CALL = { room: "call-abc123", meeting_id: 41, owner: "bob", url: "wss://calls.test", started: 1,
               transcribed: true, participants: 1 };
const JOIN: CallJoin = { session_id: "s-1", url: "wss://calls.test", token: "tok", role: "member", call: CALL };

function deps(state: CallState): CallDeps & { started: string[] } {
  const started: string[] = [];
  return {
    started,
    getCall: vi.fn(async () => state),
    startCall: vi.fn(async (_s: string, sid: string) => { started.push(sid); return { ...state, call: CALL }; }),
    joinCall: vi.fn(async () => JOIN),
  };
}

afterEach(() => { leaveCall(); cleanup(); vi.restoreAllMocks(); });

describe("enterCall", () => {
  it("starts the call when the session has none, then joins it", async () => {
    const d = deps({ session_id: "s-1", calls: true, call: null });
    await enterCall("pchat-1", "Team", d);
    expect(d.started).toEqual(["s-1"]);
    expect(activeCall()?.join.token).toBe("tok");
  });
  it("joins a live call without starting a second one", async () => {
    const d = deps({ session_id: "s-1", calls: true, call: CALL });
    await enterCall("pchat-1", "Team", d);
    expect(d.started).toEqual([]);
    expect(activeCall()?.session).toBe("pchat-1");
  });
  it("refuses where calls are not deployed", async () => {
    const d = deps({ session_id: "s-1", calls: false, call: null });
    await expect(enterCall("pchat-1", "Team", d)).rejects.toThrow(/not available/);
    expect(activeCall()).toBeNull();
  });
});

describe("the transcription notice", () => {
  it("is the room's metadata when it says, the call's own flag otherwise", () => {
    expect(transcribedNotice('{"transcribed":false}', true)).toBe(false);
    expect(transcribedNotice('{"transcribed":true}', false)).toBe(true);
    expect(transcribedNotice(undefined, true)).toBe(true);
    expect(transcribedNotice("not json", true)).toBe(true);
  });
});

describe("CallButton", () => {
  const respond = (routes: Record<string, unknown>) =>
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      const key = Object.keys(routes).find((k) => u.endsWith(k));
      if (!key) return new Response("{}", { status: 404 });
      return new Response(JSON.stringify(routes[key]), { status: 200, headers: { "Content-Type": "application/json" } });
    });

  it("is absent where calls are not deployed and nothing waits for review", async () => {
    respond({ "/call": { session_id: "s-1", calls: false, call: null }, "/proposals": { proposals: [] } });
    const { CallButton } = await import("../CallButton");
    const { container } = render(<CallButton session="s-1" label="Team" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });

  it("offers to join a live call and shows the review to the meeting's owner only", async () => {
    const prop = { id: "p-0123456789abcdef", session_id: "s-1", meeting_id: 41, owner: "bob", kind: "page",
                   workspace: "acme", path: "notes/call.md", title: "Call notes", body: "# Notes", status: "open",
                   decided_by: null, decided: null, created: 1 };
    respond({ "/call": { session_id: "s-1", calls: true, call: CALL },
              "/proposals": { you: "alice", proposals: [prop] } });
    const { CallButton } = await import("../CallButton");
    render(<CallButton session="s-1" label="Team" />);
    await waitFor(() => expect(screen.getByText(/Join call · 1/)).toBeTruthy());
    fireEvent.click(screen.getByText(/Review · 1/));
    expect(screen.getByText(/Waiting for the meeting's owner/)).toBeTruthy();
    expect(screen.queryByText("Accept")).toBeNull();
  });
});
