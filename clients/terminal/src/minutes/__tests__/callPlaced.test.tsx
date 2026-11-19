/** "Call tester-b" — the caller's tab joins the call the agent started (call-person-autojoin).
 *
 *  meeting-api ends a call nobody joins within 30 s. The agent's `call_person` started the call and
 *  replied with the room; a caller slow to open it lost the call. Pinned here, end to end on the
 *  client side:
 *   - the stream turns a `call` event into `onCall`, shape-checked;
 *   - only the view whose own message placed the call raises it (`callFromThisView`);
 *   - the shell re-reads the call and joins only the viewer's OWN call, once, opening the room first;
 *   - the join skips the pre-join bar and shows "Calling …" with Cancel until somebody else is in. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { callFromThisView, OWN_SUBMIT_WINDOW_MS, placedCall, streamChatTurn, type ChatStreamCallbacks, type PlacedCall } from "../../surfaces/chatStream";
import { __resetPlacedCalls, joinPlacedCall, readPlaced, type PlacedDeps } from "../callPlaced";
import { activeCall, callAnswered, enterCall, leaveCall, NO_ANSWER_SHOWN_MS, type ActiveCall } from "../callState";
import { RING_FOR_MS } from "../../platform/sounds";
import { CallOutgoing } from "../callkit/CallBar";
import type { CallJoin, CallState } from "../../surfaces/callsApi";

afterEach(() => { cleanup(); leaveCall(); __resetPlacedCalls(); vi.useRealTimers(); });

const CALL = { room: "call-abc123", meeting_id: 7, owner: "2", url: "wss://calls.test", started: 1, transcribed: true, participants: 0 };
const join: CallJoin = { session_id: "s-0123abcd", url: "wss://calls.test", token: "tok", role: "owner", call: CALL };
const PLACED: PlacedCall = { session: "s-0123abcd", room: "call-abc123", with: "Tester B", alreadyLive: false };

function sseResponse(chunks: string[]): Response {
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(c) { if (i < chunks.length) c.enqueue(enc.encode(chunks[i++])); else c.close(); },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}
const ev = (o: Record<string, unknown>) => `data: ${JSON.stringify(o)}\n\n`;
const noop = () => {};

describe("the stream's `call` event", () => {
  it("reaches onCall with the room, the call and the other person's name", async () => {
    const seen: PlacedCall[] = [];
    const cb: ChatStreamCallbacks = {
      onStarting: noop, onStatus: noop, onDelta: noop, onTool: noop, onCommit: noop, onRejected: noop,
      onModelFailure: noop, onError: noop, onProgress: noop, onCall: (c) => seen.push(c),
    } as ChatStreamCallbacks;
    await streamChatTurn({ prompt: "Call tester-b", session: "pchat-1", active: undefined }, cb, {
      fetchImpl: (async () => sseResponse([
        ev({ type: "tool-call", tool: "mcp__vexa__call_person", callId: "c1" }),
        ev({ type: "tool-result", callId: "c1", ok: true, summary: "{\"session_id\"" }),
        ev({ type: "call", chat: "s-0123abcd", room: "call-abc123", with: "Tester B", already_live: false }),
        ev({ type: "call", chat: "../x", room: "call-abc123" }),
        ev({ type: "turn-complete" }), ev({ type: "done", ok: true }),
      ])) as unknown as typeof fetch,
      hardTimeoutMs: 2000, signal: new AbortController().signal,
    });
    expect(seen).toEqual([PLACED]);
  });

  it("is shape-checked", () => {
    expect(placedCall({ chat: "s-1", room: "call-1" })).toEqual({ session: "s-1", room: "call-1", with: "", alreadyLive: false });
    expect(placedCall({ chat: "s 1", room: "call-1" })).toBeNull();
    expect(placedCall({ chat: "s-1" })).toBeNull();
    expect(readPlaced({ session: "s-1", room: "a/b" })).toBeNull();
  });
});

describe("whose call it is, on the client", () => {
  const now = 1_000_000_000;
  it("the view that dispatched the message owns its turn", () => {
    expect(callFromThisView({ dispatchedTurn: true, lastLocalSubmitMs: 0, now })).toBe(true);
  });
  it("another reader of the chat — an attach with nothing submitted from this tab — does not", () => {
    expect(callFromThisView({ dispatchedTurn: false, lastLocalSubmitMs: 0, now })).toBe(false);
  });
  it("a turn answering this tab's own recent submission does; a stale one does not", () => {
    expect(callFromThisView({ dispatchedTurn: false, lastLocalSubmitMs: now - 5_000, now })).toBe(true);
    expect(callFromThisView({ dispatchedTurn: false, lastLocalSubmitMs: now - OWN_SUBMIT_WINDOW_MS - 1, now })).toBe(false);
  });
});

function deps(state: Partial<CallState> & { call?: CallState["call"] }) {
  const opened: string[] = [];
  const entered: { session: string; label: string; opts: unknown }[] = [];
  const d: PlacedDeps = {
    getCall: vi.fn(async () => ({ session_id: "s-0123abcd", calls: true, you: "2", call: CALL, ...state }) as CallState),
    enter: vi.fn(async (session, label, opts) => { entered.push({ session, label, opts }); return { session, label, join } as ActiveCall; }),
    open: (s) => opened.push(s),
    active: () => null,
  };
  return { d, opened, entered };
}

describe("joining the call the agent placed", () => {
  it("opens the room, then joins as the caller — no pre-join bar, an outgoing call to them", async () => {
    const { d, opened, entered } = deps({});
    expect(await joinPlacedCall(PLACED, d)).toBe("joined");
    expect(opened).toEqual(["s-0123abcd"]);
    expect(entered).toEqual([{ session: "s-0123abcd", label: "Call with Tester B", opts: { autoJoin: true, outgoing: { to: "Tester B" } } }]);
  });

  it("joins once per call in this tab — a reconnect that hands the event over again is a no-op", async () => {
    const { d, entered } = deps({});
    await joinPlacedCall(PLACED, d);
    expect(await joinPlacedCall(PLACED, d)).toBe("already");
    expect(entered).toHaveLength(1);
  });

  it("never joins or opens someone else's call", async () => {
    const { d, opened, entered } = deps({ you: "3" });
    expect(await joinPlacedCall(PLACED, d)).toBe("not-mine");
    const older = deps({ you: undefined });
    expect(await joinPlacedCall(PLACED, older.d)).toBe("not-mine");
    expect(opened).toEqual([]); expect(entered).toEqual([]);
  });

  it("does nothing for a call that already ended or is a different one", async () => {
    const ended = deps({ call: null });
    expect(await joinPlacedCall(PLACED, ended.d)).toBe("gone");
    const other = deps({ call: { ...CALL, room: "call-other" } });
    expect(await joinPlacedCall(PLACED, other.d)).toBe("gone");
    expect(ended.opened).toEqual([]); expect(other.entered).toEqual([]);
  });

  it("opens but does not re-join a call this tab is already in", async () => {
    const { d, opened, entered } = deps({});
    d.active = () => ({ session: "s-0123abcd", label: "x", join });
    expect(await joinPlacedCall(PLACED, d)).toBe("already");
    expect(opened).toEqual(["s-0123abcd"]); expect(entered).toEqual([]);
  });
});

describe("the outgoing call in the dock", () => {
  it("enterCall carries autoJoin and the outgoing mark; somebody joining clears it", async () => {
    const callDeps = { getCall: vi.fn(async () => ({ session_id: "s-0123abcd", calls: true, call: CALL })),
      startCall: vi.fn(), joinCall: vi.fn(async () => join) };
    await enterCall("s-0123abcd", "Call with Tester B", callDeps as never, { autoJoin: true, outgoing: { to: "Tester B" } });
    expect(callDeps.startCall).not.toHaveBeenCalled();               // the agent already started it
    expect(activeCall()).toMatchObject({ autoJoin: true, outgoing: { to: "Tester B" } });
    callAnswered("s-0123abcd");
    expect(activeCall()?.outgoing).toBeUndefined();
    expect(activeCall()?.autoJoin).toBe(true);
  });

  it("a plain Start call keeps the pre-join bar (no autoJoin, no outgoing)", async () => {
    const callDeps = { getCall: vi.fn(async () => ({ session_id: "s-1", calls: true, call: CALL })),
      startCall: vi.fn(), joinCall: vi.fn(async () => join) };
    await enterCall("s-1", "Room", callDeps as never);
    expect(activeCall()?.autoJoin).toBeUndefined();
    expect(activeCall()?.outgoing).toBeUndefined();
  });

  it("says whom you are calling, and Cancel leaves", () => {
    const onCancel = vi.fn();
    render(<CallOutgoing to="Tester B" onCancel={onCancel} />);
    expect(screen.getByRole("status").textContent).toContain("Calling Tester B…");
    const cancel = screen.getByRole("button", { name: "Cancel" });
    // a kit Button, never a solid fill (UI 7r): the secondary variant
    expect(cancel.getAttribute("data-variant")).toBe("secondary");
    fireEvent.click(cancel);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  // app.dev 22 Oct: «Calling Evgeniagrani…» with Cancel still under the call at 1:11:15 — the founder
  // was in the call alone, sharing his screen; nobody answered, and the ring never ended.
  it("an unanswered ring ends with the callee's ring: «No answer», then the line goes; the call stays", async () => {
    vi.useFakeTimers();
    const callDeps = { getCall: vi.fn(async () => ({ session_id: "s-0123abcd", calls: true, call: CALL })),
      startCall: vi.fn(), joinCall: vi.fn(async () => join) };
    await enterCall("s-0123abcd", "Call with Tester B", callDeps as never, { autoJoin: true, outgoing: { to: "Tester B" } });
    expect(activeCall()?.outgoing).toMatchObject({ to: "Tester B" });
    expect(activeCall()?.outgoing?.unanswered).toBeFalsy();
    vi.advanceTimersByTime(RING_FOR_MS - 1);
    expect(activeCall()?.outgoing?.unanswered).toBeFalsy();
    vi.advanceTimersByTime(1);
    expect(activeCall()?.outgoing).toMatchObject({ to: "Tester B", unanswered: true });
    vi.advanceTimersByTime(NO_ANSWER_SHOWN_MS);
    expect(activeCall()?.outgoing).toBeUndefined();
    expect(activeCall()).toMatchObject({ session: "s-0123abcd", autoJoin: true });   // still in the call
  });

  it("an answered ring is not later marked unanswered; leaving stops the ring", async () => {
    vi.useFakeTimers();
    const callDeps = { getCall: vi.fn(async () => ({ session_id: "s-0123abcd", calls: true, call: CALL })),
      startCall: vi.fn(), joinCall: vi.fn(async () => join) };
    await enterCall("s-0123abcd", "Call with Tester B", callDeps as never, { autoJoin: true, outgoing: { to: "Tester B" } });
    callAnswered("s-0123abcd");
    vi.advanceTimersByTime(RING_FOR_MS + NO_ANSWER_SHOWN_MS);
    expect(activeCall()?.outgoing).toBeUndefined();
    await enterCall("s-0123abcd", "Call with Tester B", callDeps as never, { autoJoin: true, outgoing: { to: "Tester B" } });
    leaveCall();
    vi.advanceTimersByTime(RING_FOR_MS);
    expect(activeCall()).toBeNull();
  });

  it("says nobody answered, without Cancel (the call goes on; Leave is on the bar)", () => {
    render(<CallOutgoing to="Tester B" unanswered onCancel={() => {}} />);
    expect(screen.getByRole("status").textContent).toContain("No answer from Tester B");
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });
});
