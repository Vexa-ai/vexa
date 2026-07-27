"use client";

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
  fetchStatus?: () => Promise<AlloySttStatusResponse>;
  intervalMs?: number;
  now?: () => number;
  documentRef?: DocumentVisibility | null;
};

const STALE_AFTER_MS = 10_000;
const BACKLOG_LAG_SEC = 2;

const initialState: AlloySttTelemetryState = {
  enabled: false,
  available: false,
  aggregate: null,
  meetings: [],
  fetchedAtMs: null,
  transportError: null,
};

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

async function fetchAlloySttStatus(): Promise<AlloySttStatusResponse> {
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
  return (await response.json()) as AlloySttStatusResponse;
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
