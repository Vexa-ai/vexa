import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMeetingLive } from "../meetingLive";

/**
 * Reducer-fidelity tests for the meetingLive SSE event handler (meetingLive.ts `connect.onmessage`).
 *
 * `meetingLiveMapping.test.ts` pins the `transcript` branch; this file pins every OTHER branch — the
 * FAULT-SURFACING paths (P18/P21): a `stream-error` or an unparseable frame must each land a DISTINCT,
 * typed issue in the store, never be swallowed into silent "no data". An unknown or RETIRED event type
 * (ping, tool-call, and — since PRD decision 34 removed the in-product inference pipeline — card, note,
 * model-error, message-delta) must be ignored WITHOUT manufacturing a phantom issue.
 *
 * Harness mirrors meetingLiveMapping.test.ts: stub EventSource (jsdom has none), drive onopen+onmessage.
 */

interface MockES {
  onopen: (() => void) | null;
  onmessage: ((m: { data: string; lastEventId?: string }) => void) | null;
  onerror: (() => void) | null;
  close(): void;
}

let lastES: MockES | null = null;

class MockEventSource implements MockES {
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string; lastEventId?: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    lastES = this;
  }
  close(): void {}
}

beforeEach(() => {
  lastES = null;
  (globalThis as unknown as { EventSource: unknown }).EventSource = MockEventSource;
});

afterEach(() => {
  delete (globalThis as unknown as { EventSource?: unknown }).EventSource;
});

/** Push one already-serialized JSON payload as an SSE message (optionally with a cursor id). */
function emit(payload: Record<string, unknown>, lastEventId?: string): void {
  act(() => {
    lastES?.onopen?.();
    lastES?.onmessage?.({ data: JSON.stringify(payload), lastEventId });
  });
}

/** Push a RAW (possibly non-JSON) frame to exercise the parse-failure path. */
function emitRaw(raw: string): void {
  act(() => {
    lastES?.onopen?.();
    lastES?.onmessage?.({ data: raw });
  });
}

describe("meetingLive reducer — fault surfacing (never silent, P18/P21)", () => {
  it("records a DISTINCT stream issue on a stream-error frame", () => {
    const { result } = renderHook(() => useMeetingLive("m5", "uid-5"));
    emit({ type: "stream-error", message: "upstream 502", status: 502 });
    const issue = result.current.issues.at(-1);
    expect(issue?.kind).toBe("stream");
    expect(issue?.message).toBe("upstream 502");
    expect(issue?.status).toBe(502);
  });

  it("records a parse issue on an unparseable frame (never silently dropped)", () => {
    const { result } = renderHook(() => useMeetingLive("m8", "uid-8"));
    emitRaw("}{ not json");
    expect(result.current.issues.at(-1)?.kind).toBe("parse");
  });
});

describe("meetingLive reducer — lifecycle + inert events", () => {
  it("marks the meeting ended on meeting-end", () => {
    const { result } = renderHook(() => useMeetingLive("m9", "uid-9"));
    emit({ type: "meeting-end" });
    expect(result.current.ended).toBe(true);
  });

  it("ignores ping / tool-call and every RETIRED copilot frame without manufacturing an issue", () => {
    const { result } = renderHook(() => useMeetingLive("m10", "uid-10"));
    emit({ type: "ping" });
    emit({ type: "tool-call", text: "read foo.md" });
    // PRD decision 34: nothing produces these any more, and a client reconnecting across the deploy
    // can still be replayed one out of a redis stream. It must be inert, not a fault.
    emit({ type: "card", card: { kind: "suggestion", title: "Follow up with Acme" } });
    emit({ type: "note", note: { id: "n1", text: "cleaned line", t: 1 } });
    emit({ type: "model-error", error: { stage: "card", model: "deepseek", message: "402 unpaid" } });
    emit({ type: "message-delta", text: "thinking…" });
    expect(result.current.issues).toHaveLength(0);
    expect(result.current.transcript).toHaveLength(0);
  });

  it("remembers the SSE cursor id for a gapless reconnect", () => {
    const { result } = renderHook(() => useMeetingLive("m11", "uid-11"));
    emit({ type: "transcript", id: "seg-z", speaker: "X", text: "hi", t: 1, completed: true }, "42-0");
    // The cursor is internal; its effect is observable: the segment landed and the store stayed healthy.
    expect(result.current.transcript.find((s) => s.id === "seg-z")).toBeDefined();
    expect(result.current.issues).toHaveLength(0);
  });

  it("carries the last cursor into the reconnect URL (gapless resume after a transient drop)", () => {
    vi.useFakeTimers();
    try {
      renderHook(() => useMeetingLive("m12", "uid-12"));
      const first = lastES;
      // A segment lands carrying a cursor id, then the socket drops transiently.
      act(() => {
        first?.onopen?.();
        first?.onmessage?.({ data: JSON.stringify({ type: "transcript", id: "s1", speaker: "A", text: "hi", t: 1, completed: true }), lastEventId: "77-0" });
      });
      act(() => { first?.onerror?.(); });          // forceReconnect schedules a reconnect (RECONNECT_MS)
      act(() => { vi.advanceTimersByTime(2600); }); // > RECONNECT_MS (2500) → a NEW EventSource opens
      expect(lastES).not.toBe(first);              // reconnected with a fresh source
      expect((lastES as unknown as { url: string }).url).toContain("lid=77-0"); // carries the cursor → gapless
    } finally {
      vi.useRealTimers();
    }
  });
});
