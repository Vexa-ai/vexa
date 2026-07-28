"use client";

// ALLOY: Own the downstream STT telemetry contract boundary and polling state.

import { createStore, type ObservableStore } from "../platform/core";

export type AlloySttTelemetryError = {
  code: string;
  message: string;
};

export type AlloySttMeetingSnapshot = {
  version: 1;
  meeting_id: string;
  native_meeting_id: string;
  updated_at_ms: number;
  active_requests: number;
  active_audio_sec: number;
  waiting_channels: number;
  queued_audio_sec: number;
  latest_captured_audio_end_ms: number | null;
  latest_processed_audio_end_ms: number | null;
  lag_sec: number;
  rtf_ema: number | null;
  processed_windows: number;
  superseded_windows: number;
  last_error: AlloySttTelemetryError | null;
};

/** ALLOY: Sealed Meeting API aggregate; the Terminal only presents these global values. */
export type AlloySttAggregateHealth = "green" | "amber" | "red" | "muted";

export type AlloySttTelemetryAggregate = {
  meetings: number;
  active_requests: number;
  waiting_channels: number;
  queued_audio_sec: number;
  lag_sec: number;
  rtf: number | null;
  health: AlloySttAggregateHealth;
};

export type AlloySttStatusResponse = {
  version: 1;
  enabled: boolean;
  available: boolean;
  updated_at_ms: number;
  aggregate: AlloySttTelemetryAggregate | null;
  meetings: AlloySttMeetingSnapshot[];
  error: AlloySttTelemetryError | null;
};

export type AlloySttTelemetryState = {
  enabled: boolean;
  available: boolean;
  aggregate: AlloySttTelemetryAggregate | null;
  meetings: AlloySttMeetingSnapshot[];
  fetchedAtMs: number | null;
  transportError: string | null;
};

export type AlloySttMeetingHealth =
  | "healthy"
  | "backlogged"
  | "failed"
  | "stale";

export type AlloySttTelemetryPoller = {
  store: ObservableStore<AlloySttTelemetryState>;
  start(): void;
  stop(): void;
  pollNow(): Promise<void>;
};

type DocumentVisibility = Pick<Document, "visibilityState"> & {
  addEventListener(
    type: "visibilitychange",
    listener: EventListenerOrEventListenerObject,
  ): void;
  removeEventListener(
    type: "visibilitychange",
    listener: EventListenerOrEventListenerObject,
  ): void;
};

type AlloySttTelemetryPollerOptions = {
  fetchStatus?: () => Promise<unknown>;
  intervalMs?: number;
  now?: () => number;
  documentRef?: DocumentVisibility | null;
};

const STALE_AFTER_MS = 10_000;
const BACKLOG_LAG_SEC = 2;

type UnknownRecord = Record<string, unknown>;

const TELEMETRY_ERROR_KEYS = ["code", "message"] as const;
const AGGREGATE_KEYS = [
  "meetings",
  "active_requests",
  "waiting_channels",
  "queued_audio_sec",
  "lag_sec",
  "rtf",
  "health",
] as const;
const MEETING_KEYS = [
  "version",
  "meeting_id",
  "native_meeting_id",
  "updated_at_ms",
  "active_requests",
  "active_audio_sec",
  "waiting_channels",
  "queued_audio_sec",
  "latest_captured_audio_end_ms",
  "latest_processed_audio_end_ms",
  "lag_sec",
  "rtf_ema",
  "processed_windows",
  "superseded_windows",
  "last_error",
] as const;
const STATUS_KEYS = [
  "version",
  "enabled",
  "available",
  "updated_at_ms",
  "aggregate",
  "meetings",
  "error",
] as const;

const initialState: AlloySttTelemetryState = {
  enabled: false,
  available: false,
  aggregate: null,
  meetings: [],
  fetchedAtMs: null,
  transportError: null,
};

// ALLOY: Validate the sealed v1 response once, before untrusted JSON can enter
// the observable store or reach rendering code.
const isRecord = (value: unknown): value is UnknownRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const hasExactKeys = (
  value: UnknownRecord,
  keys: readonly string[],
): boolean => {
  const actualKeys = Object.keys(value);
  return actualKeys.length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key));
};

const isNonblankString = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

const isNonnegativeNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

const isNonnegativeInteger = (value: unknown): value is number =>
  isNonnegativeNumber(value) && Number.isInteger(value);

const isNullableNonnegativeInteger = (value: unknown): value is number | null =>
  value === null || isNonnegativeInteger(value);

const isNullableNonnegativeNumber = (value: unknown): value is number | null =>
  value === null || isNonnegativeNumber(value);

const isTelemetryError = (
  value: unknown,
): value is AlloySttTelemetryError =>
  isRecord(value) &&
  hasExactKeys(value, TELEMETRY_ERROR_KEYS) &&
  isNonblankString(value.code) &&
  isNonblankString(value.message);

const isAggregateHealth = (
  value: unknown,
): value is AlloySttAggregateHealth =>
  value === "green" ||
  value === "amber" ||
  value === "red" ||
  value === "muted";

const isAggregate = (
  value: unknown,
): value is AlloySttTelemetryAggregate =>
  isRecord(value) &&
  hasExactKeys(value, AGGREGATE_KEYS) &&
  isNonnegativeInteger(value.meetings) &&
  isNonnegativeInteger(value.active_requests) &&
  isNonnegativeInteger(value.waiting_channels) &&
  isNonnegativeNumber(value.queued_audio_sec) &&
  isNonnegativeNumber(value.lag_sec) &&
  isNullableNonnegativeNumber(value.rtf) &&
  isAggregateHealth(value.health);

const isMeetingSnapshot = (
  value: unknown,
): value is AlloySttMeetingSnapshot =>
  isRecord(value) &&
  hasExactKeys(value, MEETING_KEYS) &&
  value.version === 1 &&
  isNonblankString(value.meeting_id) &&
  isNonblankString(value.native_meeting_id) &&
  isNonnegativeInteger(value.updated_at_ms) &&
  isNonnegativeInteger(value.active_requests) &&
  isNonnegativeNumber(value.active_audio_sec) &&
  isNonnegativeInteger(value.waiting_channels) &&
  isNonnegativeNumber(value.queued_audio_sec) &&
  isNullableNonnegativeInteger(value.latest_captured_audio_end_ms) &&
  isNullableNonnegativeInteger(value.latest_processed_audio_end_ms) &&
  isNonnegativeNumber(value.lag_sec) &&
  isNullableNonnegativeNumber(value.rtf_ema) &&
  isNonnegativeInteger(value.processed_windows) &&
  isNonnegativeInteger(value.superseded_windows) &&
  (value.last_error === null || isTelemetryError(value.last_error));

const isStatusResponse = (
  value: unknown,
): value is AlloySttStatusResponse => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, STATUS_KEYS) ||
    value.version !== 1 ||
    typeof value.enabled !== "boolean" ||
    typeof value.available !== "boolean" ||
    !isNonnegativeInteger(value.updated_at_ms) ||
    !Array.isArray(value.meetings) ||
    !value.meetings.every(isMeetingSnapshot)
  ) {
    return false;
  }

  if (value.enabled && value.available) {
    return isAggregate(value.aggregate) && value.error === null;
  }
  if (!value.enabled && !value.available) {
    return value.aggregate === null &&
      value.meetings.length === 0 &&
      value.error === null;
  }
  if (value.enabled && !value.available) {
    return value.aggregate === null &&
      value.meetings.length === 0 &&
      isTelemetryError(value.error);
  }
  return false;
};

function parseAlloySttStatusResponse(
  value: unknown,
): AlloySttStatusResponse {
  if (!isStatusResponse(value)) {
    throw new Error("Invalid ALLOY STT telemetry response");
  }
  return value;
}

export function classifyAlloySttMeeting(
  meeting: AlloySttMeetingSnapshot,
  nowMs: number,
): AlloySttMeetingHealth {
  if (nowMs - meeting.updated_at_ms > STALE_AFTER_MS) return "stale";
  if (meeting.last_error) return "failed";
  if (
    meeting.waiting_channels > 0 ||
    meeting.lag_sec > BACKLOG_LAG_SEC
  ) {
    return "backlogged";
  }
  return "healthy";
}

async function fetchAlloySttStatus(): Promise<unknown> {
  const response = await fetch("/api/alloy/stt/status", { cache: "no-store" });
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = `: ${body.detail}`;
    } catch {
      // ALLOY: A non-JSON gateway failure is still reported by HTTP status.
    }
    throw new Error(`STT telemetry request failed (${response.status})${detail}`);
  }
  return response.json();
}

export function createAlloySttTelemetryPoller(
  options: AlloySttTelemetryPollerOptions = {},
): AlloySttTelemetryPoller {
  const fetchStatus = options.fetchStatus ?? fetchAlloySttStatus;
  const intervalMs = Math.max(250, options.intervalMs ?? 1_000);
  const now = options.now ?? Date.now;
  const documentRef =
    options.documentRef === undefined
      ? typeof document === "undefined"
        ? null
        : document
      : options.documentRef;
  const store = createStore<AlloySttTelemetryState>(initialState);

  let started = false;
  let timer: ReturnType<typeof setInterval> | null = null;
  let generation = 0;
  let inFlight: {
    generation: number;
    promise: Promise<void>;
  } | null = null;

  const isVisible = () =>
    documentRef === null || documentRef.visibilityState === "visible";

  const pollNow = (): Promise<void> => {
    if (!isVisible()) return Promise.resolve();
    const requestGeneration = generation;
    if (inFlight?.generation === requestGeneration) {
      return inFlight.promise;
    }

    let request!: Promise<void>;
    request = fetchStatus()
      .then(parseAlloySttStatusResponse)
      .then((status) => {
        if (generation !== requestGeneration) return;
        store.set((current) => ({
          enabled: status.enabled,
          available: status.available,
          // ALLOY: A soft transport failure retains the last valid server aggregate.
          aggregate:
            !status.available && status.error
              ? current.aggregate
              : status.aggregate,
          meetings:
            !status.available && status.error
              ? current.meetings
              : status.meetings,
          fetchedAtMs: now(),
          transportError: status.error?.message ?? null,
        }));
        if (!status.enabled) {
          stop();
        }
      })
      .catch((error: unknown) => {
        if (generation !== requestGeneration) return;
        const message =
          error instanceof Error ? error.message : "STT telemetry request failed";
        store.set((current) => ({
          ...current,
          fetchedAtMs: now(),
          transportError: message,
        }));
      })
      .finally(() => {
        if (inFlight?.promise === request) {
          inFlight = null;
        }
      });

    inFlight = {
      generation: requestGeneration,
      promise: request,
    };
    return request;
  };

  const onVisibilityChange = () => {
    if (isVisible()) void pollNow();
  };

  const start = () => {
    if (started) return;
    started = true;
    generation += 1;
    void pollNow();
    timer = setInterval(() => {
      void pollNow();
    }, intervalMs);
    documentRef?.addEventListener("visibilitychange", onVisibilityChange);
  };

  function stop() {
    if (!started) return;
    started = false;
    generation += 1;
    inFlight = null;
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    documentRef?.removeEventListener("visibilitychange", onVisibilityChange);
  }

  return { store, start, stop, pollNow };
}

export const alloySttTelemetryPoller = createAlloySttTelemetryPoller();
