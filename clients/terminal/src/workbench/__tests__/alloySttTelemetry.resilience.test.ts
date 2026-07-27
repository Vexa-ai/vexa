import { describe, expect, it } from "vitest";
import {
  createAlloySttTelemetryPoller,
  type AlloySttMeetingSnapshot,
  type AlloySttStatusResponse,
} from "../alloySttTelemetry";

const meeting: AlloySttMeetingSnapshot = {
  version: 1,
  meeting_id: "meeting-1",
  native_meeting_id: "giq-hzmp-vnn",
  updated_at_ms: 1_000,
  active_requests: 1,
  active_audio_sec: 1.25,
  waiting_channels: 2,
  queued_audio_sec: 3.5,
  latest_captured_audio_end_ms: 4_000,
  latest_processed_audio_end_ms: 2_000,
  lag_sec: 2,
  rtf_ema: 0.8,
  processed_windows: 4,
  superseded_windows: 1,
  last_error: null,
};

const status = (
  overrides: Partial<AlloySttStatusResponse> = {},
): AlloySttStatusResponse => ({
  version: 1,
  enabled: true,
  available: true,
  updated_at_ms: 1_000,
  aggregate: {
    meetings: 1,
    active_requests: 1,
    waiting_channels: 2,
    queued_audio_sec: 3.5,
    lag_sec: 2,
    rtf: 0.8,
    health: "green",
  },
  meetings: [meeting],
  error: null,
  ...overrides,
});

describe("ALLOY STT telemetry poller resilience", () => {
  it("keeps the last good meeting snapshot during a soft Redis failure", async () => {
    const responses = [
      status(),
      status({
        available: false,
        aggregate: null,
        meetings: [],
        error: {
          code: "redis_unavailable",
          message: "Redis telemetry unavailable",
        },
      }),
    ];
    const poller = createAlloySttTelemetryPoller({
      documentRef: null,
      fetchStatus: async () => responses.shift() ?? status(),
      now: () => 2_000,
    });

    await poller.pollNow();
    await poller.pollNow();

    expect(poller.store.getState()).toMatchObject({
      available: false,
      aggregate: {
        meetings: 1,
        active_requests: 1,
        waiting_channels: 2,
        queued_audio_sec: 3.5,
        lag_sec: 2,
        rtf: 0.8,
        health: "green",
      },
      meetings: [meeting],
      transportError: "Redis telemetry unavailable",
    });
  });

  it("ignores a response from a request invalidated by stop", async () => {
    let resolveRequest!: (value: AlloySttStatusResponse) => void;
    const request = new Promise<AlloySttStatusResponse>((resolve) => {
      resolveRequest = resolve;
    });
    const poller = createAlloySttTelemetryPoller({
      documentRef: null,
      fetchStatus: () => request,
      intervalMs: 60_000,
      now: () => 2_000,
    });

    poller.start();
    const oldPoll = poller.pollNow();
    poller.stop();
    resolveRequest(status());
    await oldPoll;

    expect(poller.store.getState()).toMatchObject({
      fetchedAtMs: null,
      meetings: [],
    });
  });

  it("starts a fresh request after restart while the old request is pending", async () => {
    const pending: Array<(value: AlloySttStatusResponse) => void> = [];
    const poller = createAlloySttTelemetryPoller({
      documentRef: null,
      fetchStatus: () =>
        new Promise<AlloySttStatusResponse>((resolve) => {
          pending.push(resolve);
        }),
      intervalMs: 60_000,
    });

    poller.start();
    const oldPoll = poller.pollNow();
    poller.stop();
    poller.start();
    const requestsAfterRestart = pending.length;
    pending[0](status());
    await oldPoll;
    poller.stop();

    expect(requestsAfterRestart).toBe(2);
  });
});
