import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMeetingLive } from "../meetingLive";

/**
 * Reducer-fidelity tests for the meetingLive SSE event handler (meetingLive.ts `connect.onmessage`).
 *
 * `meetingLiveMapping.test.ts` pins the `transcript` branch; this file pins every OTHER branch of the
 * merged-stream reducer — the copilot-output paths (card · note · message-delta) and, critically, the
 * FAULT-SURFACING paths (P18/P21): a `stream-error` / `model-error` / unparseable frame must each land
 * a DISTINCT, typed issue in the store — never be swallowed into silent "no data". An unknown/ignored
 * event type (ping/tool-call) must NOT manufacture a phantom issue.
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

describe("meetingLive reducer — copilot-output branches", () => {
  it("appends a proactive card", () => {
    const { result } = renderHook(() => useMeetingLive("m1", "uid-1"));
    emit({ type: "card", card: { kind: "suggestion", title: "Follow up with Acme", body: "next week" } });
    expect(result.current.cards).toHaveLength(1);
    expect(result.current.cards[0]).toMatchObject({ kind: "suggestion", title: "Follow up with Acme" });
  });

  it("upserts a processed note in place by id (no duplicate on refine)", () => {
    const { result } = renderHook(() => useMeetingLive("m2", "uid-2"));
    emit({ type: "note", note: { id: "n1", text: "draft", t: 1 } });
    emit({ type: "note", note: { id: "n1", text: "refined", t: 1, pass: 2 } });
    const matches = result.current.notes.filter((n) => n.id === "n1");
    expect(matches).toHaveLength(1);
    expect(matches[0].text).toBe("refined");
    expect(matches[0].pass).toBe(2);
  });

  it("ignores a note missing id or text (never a phantom note)", () => {
    const { result } = renderHook(() => useMeetingLive("m3", "uid-3"));
    emit({ type: "note", note: { id: "", text: "no id" } });
    emit({ type: "note", note: { id: "n2" } });
    expect(result.current.notes).toHaveLength(0);
  });

  it("captures the latest message-delta as the agent's current thought", () => {
    const { result } = renderHook(() => useMeetingLive("m4", "uid-4"));
    emit({ type: "message-delta", text: "thinking…" });
    emit({ type: "message-delta", text: "almost there" });
    expect(result.current.note).toBe("almost there");
  });
});

describe("meetingLive reducer — fault surfacing (never silent, P18/P21)", () => {
  it("records a DISTINCT stream issue on a stream-error frame", () => {
    const { result } = renderHook(() => useMeetingLive("m5", "uid-5"));
    emit({ type: "stream-error", message: "upstream 502", status: 502 });
    const issue = result.current.issues.at(-1);
    expect(issue?.kind).toBe("stream");
    expect(issue?.message).toBe("upstream 502");
    expect(issue?.status).toBe(502);
  });

  it("surfaces a model-error as an error, a typed model issue, AND a warning card (triple, not swallowed)", () => {
    const { result } = renderHook(() => useMeetingLive("m6", "uid-6"));
    emit({ type: "model-error", error: { stage: "card", model: "deepseek", message: "402 unpaid" } });
    expect(result.current.errors.at(-1)).toMatchObject({ model: "deepseek", stage: "card", message: "402 unpaid" });
    const issue = result.current.issues.at(-1);
    expect(issue?.kind).toBe("model");
    expect(issue?.model).toBe("deepseek");
    const card = result.current.cards.at(-1);
    expect(card?.kind).toBe("warning");
    expect(card?.title).toBe("Model inference error");
  });

  it("accepts a string-form model-error message", () => {
    const { result } = renderHook(() => useMeetingLive("m7", "uid-7"));
    emit({ type: "model-error", error: "boom" });
    expect(result.current.errors.at(-1)?.message).toBe("boom");
    expect(result.current.issues.at(-1)?.kind).toBe("model");
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

  it("ignores ping / tool-call frames without manufacturing an issue", () => {
    const { result } = renderHook(() => useMeetingLive("m10", "uid-10"));
    emit({ type: "ping" });
    emit({ type: "tool-call", text: "read foo.md" });
    expect(result.current.issues).toHaveLength(0);
    expect(result.current.cards).toHaveLength(0);
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
