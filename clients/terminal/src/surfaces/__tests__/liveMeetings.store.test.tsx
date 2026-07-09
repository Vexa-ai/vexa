/** Behavioral test for the meetings STORE (liveMeetings.ts), driven over its real seams:
 *    GET /api/meetings (mocked fetch) seeds the rows, then meeting.status frames arrive over the gateway
 *    WS (mocked WebSocket) and patch the store. Pins the WS frame to the ws.v1 golden fixture.
 *
 *  (a) a golden frame patches the matching row by `native` (status flips, no re-snapshot from the frame);
 *  (b) an UNKNOWN-row frame triggers a re-snapshot (extra GET /api/meetings) so a freshly-created
 *      scheduled/idle meeting surfaces.
 *  (c) a WS connect re-snapshots so a missed status frame is repaired by the backend state.
 *
 *  The store is a module singleton, so each test re-imports it fresh (vi.resetModules) and installs its
 *  own fetch + WebSocket fakes BEFORE importing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import golden from "./fixtures/MeetingStatus.scheduled.json";

// The golden's native — the seeded row uses the SAME native so the frame matches by `native`.
const NATIVE = golden.native; // "abc-defg-hij"

function meetingsPayload(status: string) {
  return {
    meetings: [
      {
        id: golden.meeting_id,
        platform: "google_meet",
        native_meeting_id: NATIVE,
        status,
        start_time: null,
        end_time: null,
        data: {},
      },
    ],
  };
}

// A fake WebSocket that captures the store's onmessage handler so the test can push frames.
class FakeWebSocket {
  static last: FakeWebSocket | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.last = this;
    // open on next tick so listeners are wired
    queueMicrotask(() => this.onopen?.());
  }
  close() {}
  push(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.resetModules();
  FakeWebSocket.last = null;
  // @ts-expect-error — install the fake transport for the store under test
  globalThis.WebSocket = FakeWebSocket;
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const u = String(input);
    if (u.includes("/api/ws")) return jsonResp({ url: "ws://test/ws" });
    if (u.includes("/api/meetings")) return jsonResp(meetingsPayload("idle"));
    return jsonResp({});
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function jsonResp(body: unknown) {
  return { ok: true, json: async () => body } as Response;
}

async function startStore() {
  const mod = await import("../liveMeetings");
  const hook = renderHook(() => mod.useLiveMeetings());
  // wait until the initial snapshot seeded the row AND the WS opened
  await waitFor(() => {
    expect(hook.result.current.length).toBe(1);
    expect(FakeWebSocket.last).not.toBeNull();
  });
  return { mod, hook };
}

describe("liveMeetings store", () => {
  it("(a) patches the matching row by `native` from a golden frame", async () => {
    const { hook } = await startStore();
    expect(hook.result.current[0].live_status).toBe("idle");
    const before = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/api/meetings")).length;

    await act(async () => {
      FakeWebSocket.last!.push(golden); // status: "scheduled", native: NATIVE
    });

    await waitFor(() => expect(hook.result.current[0].live_status).toBe("scheduled"));
    const row = hook.result.current[0];
    expect(row.native_id).toBe(NATIVE);
    expect(row.scheduled_at).toBe(golden.when); // when carried into scheduled_at
    // patched IN PLACE — the frame itself did not trigger a snapshot fetch.
    const after = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/api/meetings")).length;
    expect(after).toBe(before);
  });

  it("(b) re-snapshots when the frame targets an UNKNOWN row", async () => {
    const { hook } = await startStore();
    const before = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/api/meetings")).length;

    await act(async () => {
      FakeWebSocket.last!.push({ ...golden, native: "zzz-unknown-row", meeting_id: 999 });
    });

    // unknown row → applyFrame falls through to snapshot() → another GET /api/meetings
    await waitFor(() => {
      const now = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/api/meetings")).length;
      expect(now).toBe(before + 1);
    });
    // the unknown native did NOT create a phantom row
    expect(hook.result.current.every((m) => m.native_id !== "zzz-unknown-row")).toBe(true);
  });

  it("(c) re-snapshots on WS connect so a missed status advance is repaired", async () => {
    let meetingSnapshots = 0;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const u = String(input);
      if (u.includes("/api/meetings")) {
        meetingSnapshots += 1;
        return jsonResp(meetingsPayload(meetingSnapshots === 1 ? "requested" : "active"));
      }
      return jsonResp({});
    });

    const { hook } = await startStore();

    await waitFor(() => expect(hook.result.current[0].live_status).toBe("active"));
    const snapshotCalls = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/api/meetings"));
    expect(snapshotCalls.length).toBeGreaterThanOrEqual(2);
  });

  it("(d) a 'deleted' frame REMOVES the row (a retired plan never masquerades as Recorded)", async () => {
    const { hook } = await startStore();
    expect(hook.result.current.length).toBe(1);

    await act(async () => {
      FakeWebSocket.last!.push({ ...golden, status: "deleted", when: null });
    });

    await waitFor(() => expect(hook.result.current.length).toBe(0));
  });
});
