/**
 * ALLOY: Real gmeet-pipeline regression for CPU STT backpressure.
 *
 * The real pipeline and SpeakerStreamManager run unchanged. Only the external
 * STT boundary is controlled so the first request can remain pending while the
 * same Google Meet audio channel rotates through later turns.
 *
 * Break caught: dispatching overlapping STT requests for successive turns on
 * one channel, which grows an obsolete request queue faster than CPU Whisper
 * can drain it.
 */
import {
  createAlloySttTelemetryTracker,
  createGmeetPipeline,
  type AlloySttTelemetrySnapshotV1,
  type TranscriptSink,
} from "./index.js";
import type { TranscriptionResult } from "@vexa/transcribe-whisper";

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const pcm = (marker: number) => new Float32Array(400).fill(marker);

let releaseFirst!: () => void;
const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
const started: number[] = [];
let active = 0;
let peakActive = 0;

const result = (marker: number, duration: number): TranscriptionResult => ({
  text: `alloy channel sample ${marker}`,
  language: "en",
  language_probability: 1,
  duration,
  segments: [{ start: 0, end: duration, text: `alloy channel sample ${marker}` }],
});

const transcribe = async (audio: Float32Array): Promise<TranscriptionResult> => {
  const marker = Math.round(audio[audio.length - 1] * 10);
  started.push(marker);
  active++;
  peakActive = Math.max(peakActive, active);
  try {
    if (started.length === 1) await firstGate;
    return result(marker, audio.length / 16000);
  } finally {
    active--;
  }
};

const sink: TranscriptSink = {
  segment: () => {},
  draft: () => {},
  finalize: () => {},
};

const tracker = createAlloySttTelemetryTracker({
  meetingId: "501",
  nativeMeetingId: "alloy-backpressure-room",
});

const expectSnapshot = (
  label: string,
  expected: Partial<AlloySttTelemetrySnapshotV1>,
) => {
  const snapshot = tracker.snapshot();
  for (const [key, value] of Object.entries(expected)) {
    const actual = snapshot[key as keyof AlloySttTelemetrySnapshotV1];
    if (actual !== value) {
      throw new Error(`${label}: expected ${key}=${value}, got ${String(actual)}`);
    }
  }
};

async function run() {
  const pipe = createGmeetPipeline({
    transcribe,
    sink,
    serializeTranscriptionByChannel: true,
    alloySttTelemetry: tracker,
    config: {
      minAudioDuration: 0.01,
      submitInterval: 0.01,
      confirmThreshold: 1,
    },
  });

  pipe.feedAudio(0, "Alice", pcm(0.1), 0);
  await sleep(30);
  if (started.length !== 1) throw new Error(`expected first STT request, got ${started.length}`);
  expectSnapshot("first request active", {
    active_requests: 1,
    waiting_channels: 0,
  });

  pipe.feedAudio(0, "Bob", pcm(0.2), 30);
  await sleep(30);
  expectSnapshot("second request pending", {
    active_requests: 1,
    waiting_channels: 1,
    superseded_windows: 0,
  });

  pipe.feedAudio(0, "Carol", pcm(0.3), 60);
  await sleep(30);
  expectSnapshot("third request supersedes second", {
    active_requests: 1,
    waiting_channels: 1,
    superseded_windows: 1,
  });

  if (peakActive !== 1) {
    throw new Error(`same channel dispatched ${peakActive} concurrent STT requests`);
  }

  releaseFirst();
  await sleep(80);

  if (started.length !== 2 || started[1] !== 3) {
    throw new Error(`expected latest pending turn only; started markers=${started.join(",")}`);
  }

  await pipe.dispose();
  expectSnapshot("queue drained", {
    active_requests: 0,
    waiting_channels: 0,
    processed_windows: 2,
  });
  console.log("PASS ALLOY channel backpressure serializes and coalesces same-channel turns");
}

run().catch((error) => {
  releaseFirst?.();
  console.error(error);
  process.exit(1);
});
