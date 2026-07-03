import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMeetingLive, type LiveSegment } from "../meetingLive";

/**
 * Contract-fidelity regression test for the live transcript wire->LiveSegment mapping.
 *
 * The original bug: a `completed:false` (pending / in-progress ASR) transcript event mapped
 * through to a segment that had its `completed` flag stripped, so the downstream meeting-render
 * path (useMeeting -> buildProcessedNotes) could no longer tell pending from finalized. The wire
 * timestamp-derived `tsMs` likewise had to survive.
 *
 * `useMeetingLive(meetingId, sessionUid)` opens an EventSource and accumulates events into a
 * LiveState. We stub EventSource (jsdom has none) so we can push a synthetic `transcript` event
 * and assert the resulting LiveSegment preserves `completed` and carries an absolute `tsMs`.
 */

interface MockES {
  onopen: (() => void) | null;
  onmessage: ((m: { data: string }) => void) | null;
  onerror: (() => void) | null;
  close(): void;
}

let lastES: MockES | null = null;

class MockEventSource implements MockES {
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
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

function emit(payload: Record<string, unknown>): void {
  act(() => {
    lastES?.onopen?.();
    lastES?.onmessage?.({ data: JSON.stringify(payload) });
  });
}

describe("live transcript wire->segment mapping", () => {
  it("preserves completed:false (pending) and derives tsMs", () => {
    const { result } = renderHook(() => useMeetingLive("m1", "uid-1"));

    expect(lastES, "EventSource should open once sessionUid is real").not.toBeNull();

    emit({ type: "transcript", id: "seg-a", speaker: "Jane", text: "in progress", t: 12, completed: false });

    const seg = result.current.transcript.find((s: LiveSegment) => s.id === "seg-a");
    expect(seg, "pending transcript segment must reach the store").toBeDefined();
    expect(seg?.completed).toBe(false);
    expect(typeof seg?.tsMs).toBe("number");
    expect(Number.isFinite(seg?.tsMs)).toBe(true);
  });

  it("treats a finalized transcript (completed omitted or true) as completed:true", () => {
    const { result } = renderHook(() => useMeetingLive("m2", "uid-2"));

    emit({ type: "transcript", id: "seg-b", speaker: "Bob", text: "done", t: 3, completed: true });
    emit({ type: "transcript", id: "seg-c", speaker: "Bob", text: "also done", t: 4 });

    const b = result.current.transcript.find((s: LiveSegment) => s.id === "seg-b");
    const c = result.current.transcript.find((s: LiveSegment) => s.id === "seg-c");
    expect(b?.completed).toBe(true);
    // completed omitted on the wire => finalized (completed !== false).
    expect(c?.completed).toBe(true);
  });

  it("upserts a pending segment in place and flips it to completed on finalize", () => {
    const { result } = renderHook(() => useMeetingLive("m3", "uid-3"));

    emit({ type: "transcript", id: "seg-d", speaker: "Cara", text: "live...", t: 5, completed: false });
    emit({ type: "transcript", id: "seg-d", speaker: "Cara", text: "live final", t: 5, completed: true });

    const matches = result.current.transcript.filter((s: LiveSegment) => s.id === "seg-d");
    expect(matches).toHaveLength(1);
    expect(matches[0].completed).toBe(true);
    expect(matches[0].text).toBe("live final");
  });
});
