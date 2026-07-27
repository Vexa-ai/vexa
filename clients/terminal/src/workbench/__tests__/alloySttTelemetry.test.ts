import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  classifyAlloySttMeeting,
  createAlloySttTelemetryPoller,
  type AlloySttStatusResponse,
} from "../alloySttTelemetry";

const response = (updatedAtMs: number): AlloySttStatusResponse => ({
  version: 1,
  enabled: true,
  available: true,
  updated_at_ms: updatedAtMs,
  meetings: [{
    version: 1,
    meeting_id: "41",
    native_meeting_id: "abc-defg-hij",
    updated_at_ms: updatedAtMs,
    active_requests: 1,
    active_audio_sec: 2,
    waiting_channels: 1,
    queued_audio_sec: 3,
    latest_captured_audio_end_ms: 10_000,
    latest_processed_audio_end_ms: 7_000,
    lag_sec: 3,
    rtf_ema: 0.8,
    processed_windows: 4,
    superseded_windows: 2,
    last_error: null,
  }],
  error: null,
});

describe("ALLOY STT telemetry poller", () => {
  let visibility = "visible";

  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockImplementation(
      () => visibility as DocumentVisibilityState,
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("uses one in-flight request, pauses while hidden, and refreshes on visibility", async () => {
    let now = 1_000;
    const fetchStatus = vi.fn(async () => response(now));
    const poller = createAlloySttTelemetryPoller({
      fetchStatus,
      now: () => now,
      intervalMs: 1_000,
    });

    poller.start();
    await poller.pollNow();
    expect(fetchStatus).toHaveBeenCalledTimes(1);
    expect(poller.store.getState().meetings[0].meeting_id).toBe("41");

    now = 2_000;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(fetchStatus).toHaveBeenCalledTimes(2);

    visibility = "hidden";
    now = 5_000;
    await vi.advanceTimersByTimeAsync(3_000);
    expect(fetchStatus).toHaveBeenCalledTimes(2);

    visibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    await poller.pollNow();
    expect(fetchStatus).toHaveBeenCalledTimes(3);

    poller.stop();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(fetchStatus).toHaveBeenCalledTimes(3);
  });

  it("keeps the last snapshot when the next request fails", async () => {
    const fetchStatus = vi.fn()
      .mockResolvedValueOnce(response(1_000))
      .mockRejectedValueOnce(new Error("gateway down"));
    const poller = createAlloySttTelemetryPoller({ fetchStatus, now: () => 2_000 });

    await poller.pollNow();
    await poller.pollNow();

    const state = poller.store.getState();
    expect(state.meetings).toHaveLength(1);
    expect(state.transportError).toBe("gateway down");
  });

  it("stops polling after the server reports telemetry disabled", async () => {
    const disabled = { ...response(1_000), enabled: false, available: false, meetings: [] };
    const fetchStatus = vi.fn(async () => disabled);
    const poller = createAlloySttTelemetryPoller({ fetchStatus, intervalMs: 1_000 });

    poller.start();
    await poller.pollNow();
    await vi.advanceTimersByTimeAsync(3_000);

    expect(fetchStatus).toHaveBeenCalledOnce();
  });
});

describe("ALLOY STT meeting state", () => {
  it("distinguishes healthy, backlogged, failed, and stale snapshots", () => {
    const base = response(10_000).meetings[0];
    expect(classifyAlloySttMeeting(base, 12_000)).toBe("backlogged");
    expect(classifyAlloySttMeeting({ ...base, waiting_channels: 0, lag_sec: 0 }, 12_000)).toBe("healthy");
    expect(classifyAlloySttMeeting({ ...base, last_error: { code: "stt", message: "failed" } }, 12_000)).toBe("failed");
    expect(classifyAlloySttMeeting(base, 20_001)).toBe("stale");
  });
});
