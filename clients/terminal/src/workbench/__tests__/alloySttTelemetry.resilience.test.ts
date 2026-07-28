// ALLOY: Regression coverage for the downstream STT telemetry polling boundary.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
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

const STATUS_GOLDEN_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "..",
  "..",
  "core",
  "meetings",
  "contracts",
  "alloy-stt-telemetry.v1",
  "golden",
);

describe("ALLOY STT telemetry poller resilience", () => {
  const validStatus = status();
  const validAggregate = validStatus.aggregate!;
  const { health: _health, ...aggregateWithoutHealth } = validAggregate;
  const invalidStatuses: Array<[string, unknown]> = [
    [
      "rejects a non-finite aggregate RTF",
      {
        ...validStatus,
        aggregate: { ...validAggregate, rtf: Number.POSITIVE_INFINITY },
      },
    ],
    [
      "rejects a negative duration",
      {
        ...validStatus,
        meetings: [{ ...meeting, active_audio_sec: -0.1 }],
      },
    ],
    [
      "rejects a fractional integer counter",
      {
        ...validStatus,
        aggregate: { ...validAggregate, active_requests: 0.5 },
      },
    ],
    [
      "rejects an unknown health enum",
      {
        ...validStatus,
        aggregate: { ...validAggregate, health: "blue" },
      },
    ],
    [
      "rejects an available response with a null aggregate",
      { ...validStatus, aggregate: null },
    ],
    [
      "rejects a non-array meetings field",
      { ...validStatus, meetings: {} },
    ],
    [
      "rejects a blank meeting identifier",
      {
        ...validStatus,
        meetings: [{ ...meeting, native_meeting_id: "   " }],
      },
    ],
    [
      "rejects a missing required aggregate field",
      { ...validStatus, aggregate: aggregateWithoutHealth },
    ],
    [
      "rejects a future schema version",
      { ...validStatus, version: 2 },
    ],
    [
      "rejects an unsealed extra field",
      { ...validStatus, unexpected: true },
    ],
  ];

  it.each(invalidStatuses)(
    "%s without replacing the last good snapshot",
    async (_name, invalidStatus) => {
      const responses = [status(), invalidStatus];
      const poller = createAlloySttTelemetryPoller({
        documentRef: null,
        fetchStatus: async () => responses.shift(),
        now: () => 2_000,
      });

      await poller.pollNow();
      const lastGood = poller.store.getState();
      await expect(poller.pollNow()).resolves.toBeUndefined();

      const current = poller.store.getState();
      expect(current.aggregate).toEqual(lastGood.aggregate);
      expect(current.meetings).toEqual(lastGood.meetings);
      expect(current.transportError).toMatch(/invalid.*telemetry/i);
    },
  );

  it.each([
    ["StatusResponse.available.json", true, true, 1, true, false],
    ["StatusResponse.unavailable.json", true, false, 0, false, true],
    ["StatusResponse.disabled.json", false, false, 0, false, false],
  ] as const)(
    "accepts the sealed %s golden",
    async (
      file,
      expectedEnabled,
      expectedAvailable,
      expectedMeetings,
      expectedAggregate,
      expectedBackendError,
    ) => {
      const golden = JSON.parse(
        readFileSync(join(STATUS_GOLDEN_DIR, file), "utf8"),
      ) as unknown;
      const poller = createAlloySttTelemetryPoller({
        documentRef: null,
        fetchStatus: async () => golden,
        now: () => 2_000,
      });

      await expect(poller.pollNow()).resolves.toBeUndefined();

      const current = poller.store.getState();
      expect(current.enabled).toBe(expectedEnabled);
      expect(current.available).toBe(expectedAvailable);
      expect(current.meetings).toHaveLength(expectedMeetings);
      expect(current.aggregate !== null).toBe(expectedAggregate);
      if (expectedBackendError) {
        expect(current.transportError).toBeTruthy();
        expect(current.transportError).not.toMatch(/invalid.*telemetry/i);
      } else {
        expect(current.transportError).toBeNull();
      }
    },
  );

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
