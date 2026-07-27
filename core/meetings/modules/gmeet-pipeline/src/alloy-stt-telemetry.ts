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
  queued(channelId: string, audioSec: number): void;
  superseded(channelId: string, audioSec: number): void;
  started(channelId: string, audioSec: number): void;
  completed(input: {
    channelId: string;
    audioSec: number;
    audioEndMs: number;
    processingDurationMs: number;
  }): void;
  failed(channelId: string, error: AlloySttTelemetryError): void;
  recovered(): void;
  snapshot(): AlloySttTelemetrySnapshotV1;
}

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
  const pendingByChannel = new Map<string, number>();
  const activeByChannel = new Map<string, number>();

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
      active_requests: activeByChannel.size,
      active_audio_sec: sum(activeByChannel.values()),
      waiting_channels: pendingByChannel.size,
      queued_audio_sec: sum(pendingByChannel.values()),
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

    queued(channelId, audioSec) {
      pendingByChannel.set(channelId, nonNegative(audioSec));
      touch();
    },

    superseded(channelId, audioSec) {
      pendingByChannel.set(channelId, nonNegative(audioSec));
      supersededWindows++;
      touch();
    },

    started(channelId, audioSec) {
      pendingByChannel.delete(channelId);
      activeByChannel.set(channelId, nonNegative(audioSec));
      touch();
    },

    completed({ channelId, audioSec, audioEndMs, processingDurationMs }) {
      activeByChannel.delete(channelId);
      const normalizedEnd = nonNegative(audioEndMs);
      latestProcessedAudioEndMs =
        latestProcessedAudioEndMs === null
          ? normalizedEnd
          : Math.max(latestProcessedAudioEndMs, normalizedEnd);
      processedWindows++;

      if (Number.isFinite(audioSec) && audioSec > 0 && Number.isFinite(processingDurationMs)) {
        const requestRtf = nonNegative(processingDurationMs) / 1000 / audioSec;
        rtfEma = rtfEma === null ? requestRtf : 0.2 * requestRtf + 0.8 * rtfEma;
      }
      touch();
    },

    failed(channelId, error) {
      activeByChannel.delete(channelId);
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
