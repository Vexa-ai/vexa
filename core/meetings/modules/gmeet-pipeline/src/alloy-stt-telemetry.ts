/** ALLOY: local STT queue snapshot and request-lifecycle contract. */
export type AlloySttTelemetryError = {
  code: string;
  message: string;
};

export type AlloySttTelemetrySnapshotV1 = {
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

export interface AlloySttTelemetryTracker {
  captured(channelId: string, audioEndMs: number): void;
  queued(requestId: string, channelId: string, audioSec: number): void;
  superseded(requestId: string): void;
  started(requestId: string, channelId: string, audioSec: number): void;
  finished(requestId: string): void;
  completed(input: {
    requestId: string;
    audioSec: number;
    audioEndMs: number;
    executionDurationMs?: number;
  }): void;
  failed(requestId: string, error: AlloySttTelemetryError): void;
  recovered(): void;
  snapshot(): AlloySttTelemetrySnapshotV1;
}

/** ALLOY: request metadata only; PCM stays owned by the scheduler. */
type AlloySttRequestRecord = {
  channelId: string;
  audioSec: number;
};

const nonNegative = (value: number): number =>
  Number.isFinite(value) ? Math.max(0, value) : 0;

const sum = (values: Iterable<number>): number => {
  let total = 0;
  for (const value of values) total += value;
  return total;
};

export function createAlloySttTelemetryTracker(input: {
  meetingId: string;
  nativeMeetingId: string;
  now?: () => number;
}): AlloySttTelemetryTracker {
  const now = input.now ?? Date.now;
  // ALLOY: request identity keeps a limiter waiter and a newer scheduler
  // window on the same channel without either record overwriting the other.
  const pendingByRequest = new Map<string, AlloySttRequestRecord>();
  const activeByRequest = new Map<string, AlloySttRequestRecord>();

  let updatedAtMs = now();
  let latestCapturedAudioEndMs: number | null = null;
  let latestProcessedAudioEndMs: number | null = null;
  let rtfEma: number | null = null;
  let processedWindows = 0;
  let supersededWindows = 0;
  let lastError: AlloySttTelemetryError | null = null;

  const touch = () => {
    updatedAtMs = now();
  };

  const snapshot = (): AlloySttTelemetrySnapshotV1 => {
    const lagSec =
      latestCapturedAudioEndMs === null || latestProcessedAudioEndMs === null
        ? 0
        : Math.max(0, (latestCapturedAudioEndMs - latestProcessedAudioEndMs) / 1000);

    return {
      version: 1,
      meeting_id: input.meetingId,
      native_meeting_id: input.nativeMeetingId,
      updated_at_ms: updatedAtMs,
      active_requests: activeByRequest.size,
      active_audio_sec: sum([...activeByRequest.values()].map((record) => record.audioSec)),
      waiting_channels: new Set(
        [...pendingByRequest.values()].map((record) => record.channelId),
      ).size,
      queued_audio_sec: sum([...pendingByRequest.values()].map((record) => record.audioSec)),
      latest_captured_audio_end_ms: latestCapturedAudioEndMs,
      latest_processed_audio_end_ms: latestProcessedAudioEndMs,
      lag_sec: lagSec,
      rtf_ema: rtfEma,
      processed_windows: processedWindows,
      superseded_windows: supersededWindows,
      last_error: lastError,
    };
  };

  return {
    captured(_channelId, audioEndMs) {
      const normalized = nonNegative(audioEndMs);
      latestCapturedAudioEndMs =
        latestCapturedAudioEndMs === null
          ? normalized
          : Math.max(latestCapturedAudioEndMs, normalized);
      touch();
    },

    queued(requestId, channelId, audioSec) {
      pendingByRequest.set(requestId, {
        channelId,
        audioSec: nonNegative(audioSec),
      });
      touch();
    },

    superseded(requestId) {
      if (pendingByRequest.delete(requestId)) supersededWindows++;
      touch();
    },

    started(requestId, channelId, audioSec) {
      pendingByRequest.delete(requestId);
      activeByRequest.set(requestId, {
        channelId,
        audioSec: nonNegative(audioSec),
      });
      touch();
    },

    finished(requestId) {
      activeByRequest.delete(requestId);
      touch();
    },

    completed({ requestId, audioSec, audioEndMs, executionDurationMs }) {
      // ALLOY: custom/injected transcribers may ignore the optional observer.
      // Completion still drains their exact request without inventing execution time.
      pendingByRequest.delete(requestId);
      activeByRequest.delete(requestId);
      const normalizedEnd = nonNegative(audioEndMs);
      latestProcessedAudioEndMs =
        latestProcessedAudioEndMs === null
          ? normalizedEnd
          : Math.max(latestProcessedAudioEndMs, normalizedEnd);
      processedWindows++;

      if (
        Number.isFinite(audioSec)
        && audioSec > 0
        && executionDurationMs !== undefined
        && Number.isFinite(executionDurationMs)
      ) {
        const requestRtf = nonNegative(executionDurationMs) / 1000 / audioSec;
        rtfEma = rtfEma === null ? requestRtf : 0.2 * requestRtf + 0.8 * rtfEma;
      }
      touch();
    },

    failed(requestId, error) {
      // ALLOY: clear only the failed request; unrelated waiters remain visible.
      pendingByRequest.delete(requestId);
      activeByRequest.delete(requestId);
      lastError = error;
      touch();
    },

    recovered() {
      lastError = null;
      touch();
    },

    snapshot,
  };
}
