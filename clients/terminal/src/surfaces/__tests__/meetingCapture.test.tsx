/** #1670 — a past meeting that captured audio must not read "nothing captured".
 *
 *  Reported from a self-hosted compose instance: every past row in the Meetings view carried the
 *  phrase, including a Teams meeting with 14 transcript segments and a 741 KB recording. The core's
 *  list omits `data.recordings` (it is heavy, #584) while the detail path keeps it, so the client's
 *  derivation — `!!(data.recordings?.length)` — read `undefined` on every row.
 *
 *  The core now hoists the answer onto the row as `has_capture`. These tests drive the REAL store
 *  over its real seam (GET /api/meetings), because the defect lived in the mapping between that
 *  response and the row the view renders, not in either end alone.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { pastPhrase } from "../today";
import type { MeetingMock } from "../meetingModel";

function jsonResp(body: unknown) {
  return { ok: true, json: async () => body } as Response;
}

/** One completed Teams row, shaped as the LIST ships it — `data.recordings` omitted by the core. */
function row(id: number, native: string, extra: Record<string, unknown>) {
  return {
    id, platform: "teams", native_meeting_id: native, status: "completed",
    start_time: "2026-09-12T09:00:00Z", end_time: "2026-09-12T09:30:00Z",
    data: { recording_enabled: true, segments_captured: 14 },
    ...extra,
  };
}

let rows: unknown[] = [];

beforeEach(() => {
  vi.resetModules();
  // @ts-expect-error — the store opens a WS on mount; a no-op transport keeps the test to the fetch seam.
  globalThis.WebSocket = class { constructor() {} close() {} } as unknown;
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const u = String(input);
    if (u.includes("/api/ws")) return jsonResp({ url: "ws://test/ws" });
    if (u.includes("/api/meetings")) return jsonResp({ meetings: rows });
    return jsonResp({});
  }) as unknown as typeof fetch;
});

afterEach(() => { vi.restoreAllMocks(); });

async function seeded() {
  const mod = await import("../liveMeetings");
  const hook = renderHook(() => mod.useLiveMeetings());
  await waitFor(() => expect(hook.result.current.length).toBe(rows.length));
  return Object.fromEntries(hook.result.current.map((m) => [m.native_id, m]));
}

describe("capture status on a past meeting row", () => {
  it("believes the core's answer — a captured meeting reads as captured", async () => {
    rows = [row(1, "captured", { has_capture: true })];
    expect((await seeded())["captured"].has_capture).toBe(true);
  });

  it("believes the core's answer when it is NO — an empty meeting stays empty", async () => {
    // `recording_enabled: true` in `data` is the SETTING and must not sway the row: a bot that
    // never joined carries it too. Only the core's evidence-derived answer counts.
    rows = [row(2, "empty", { has_capture: false })];
    expect((await seeded())["empty"].has_capture).toBe(false);
  });

  it("falls back to the evidence the list still carries when the core predates has_capture", async () => {
    // A terminal talking to an older core gets no `has_capture`. `segments_captured` is light and
    // survives the list projection, so the row is still answerable — this is the reported instance.
    rows = [
      row(3, "old-captured", {}),                                   // segments_captured: 14
      row(4, "old-empty", { data: { recording_enabled: true, segments_captured: 0 } }),
      row(5, "old-audio-only", { data: { recordings: [{ id: "r1" }] } }),
    ];
    const byNative = await seeded();
    expect(byNative["old-captured"].has_capture).toBe(true);
    expect(byNative["old-empty"].has_capture).toBe(false);
    expect(byNative["old-audio-only"].has_capture).toBe(true);
  });
});

describe("the phrase a past row carries", () => {
  const run = (has_capture: boolean) => ({ id: "1", title: "t", when: "", status: "past",
    has_capture } as MeetingMock);

  it("does NOT say 'nothing captured' about a meeting that captured something", () => {
    // The reported symptom, at the surface that produced it: this row had 14 segments and a webm.
    expect(pastPhrase(run(true), true)?.text).toBe("recap ready");
    expect(pastPhrase(run(true), false)).toBeNull();      // reviewed → the row goes quiet
  });

  it("still says it about a meeting that genuinely captured nothing", () => {
    // The phrase is a failure report and has to keep working as one.
    expect(pastPhrase(run(false), true)?.text).toBe("nothing captured");
    expect(pastPhrase(run(false), false)?.text).toBe("nothing captured");
  });
});
