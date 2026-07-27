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
  type TranscriptSink,
} from "./index.js";
import type {
  TranscriptionExecutionObserver,
  TranscriptionResult,
} from "@vexa/transcribe-whisper";

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const pcm = (marker: number) => new Float32Array(400).fill(marker);
const waitFor = async (condition: () => boolean, detail: string): Promise<void> => {
  const deadline = Date.now() + 1_000;
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error(`timed out waiting for ${detail}`);
    await sleep(5);
  }
};

let releaseFirst!: () => void;
const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
const started: number[] = [];
const completedSpeakers: string[] = [];
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
  // ALLOY: observe completed transcript output at the production sink boundary.
  segment: (segment) => { completedSpeakers.push(segment.speaker); },
  draft: () => {},
  finalize: () => {},
};

const tracker = createAlloySttTelemetryTracker({
  meetingId: "501",
  nativeMeetingId: "alloy-backpressure-room",
});

async function runRotationRegression() {
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

  pipe.feedAudio(0, "Bob", pcm(0.2), 30);
  await sleep(30);

  releaseFirst();
  await sleep(80);

  await pipe.dispose();

  // ALLOY: the active Alice turn owns its deferred final resubmit; Bob remains
  // the latest pending turn and runs only after Alice has finalized.
  const observedSpeakers = completedSpeakers.join(",");
  if (observedSpeakers !== "Alice,Bob") {
    throw new Error(`expected completed speakers Alice,Bob exactly once; got ${observedSpeakers || "<none>"}`);
  }
  if (started.join(",") !== "1,1,2") {
    throw new Error(`expected Alice active+final then Bob pending; started markers=${started.join(",")}`);
  }
  if (peakActive !== 1) {
    throw new Error(`same channel dispatched ${peakActive} concurrent STT requests`);
  }
  const finalSnapshot = tracker.snapshot();
  if (finalSnapshot.active_requests !== 0 || finalSnapshot.waiting_channels !== 0) {
    throw new Error(
      `expected drained telemetry; active=${finalSnapshot.active_requests} waiting=${finalSnapshot.waiting_channels}`,
    );
  }
  console.log("PASS ALLOY channel backpressure preserves the active turn before the latest pending turn");
}

async function runLimiterObserverTelemetry() {
  // ALLOY: The injected STT boundary models one real limiter slot. Queue delay is
  // intentionally much larger than the literal slot-held duration reported to telemetry.
  let occupied = false;
  const slotWaiters: Array<() => void> = [];
  const executionMarkers: number[] = [];
  let missingObserver = false;
  let releaseFirstExecution!: () => void;
  let releaseSecondExecution!: () => void;
  const firstExecutionGate = new Promise<void>((resolve) => { releaseFirstExecution = resolve; });
  const secondExecutionGate = new Promise<void>((resolve) => { releaseSecondExecution = resolve; });

  const acquire = async (observer: TranscriptionExecutionObserver): Promise<void> => {
    if (occupied) {
      observer.waiting();
      await new Promise<void>((resolve) => slotWaiters.push(resolve));
    } else {
      occupied = true;
    }
    observer.started();
  };
  const release = (observer: TranscriptionExecutionObserver): void => {
    observer.finished(5);
    const next = slotWaiters.shift();
    if (next) next();
    else occupied = false;
  };

  const observedTranscribe = async (
    audio: Float32Array,
    _prompt?: string,
    observer?: TranscriptionExecutionObserver,
  ): Promise<TranscriptionResult> => {
    if (!observer) {
      missingObserver = true;
      throw new Error("ALLOY limiter observer was not forwarded");
    }
    await acquire(observer);
    const marker = Math.round(audio[audio.length - 1] * 10);
    executionMarkers.push(marker);
    try {
      if (executionMarkers.length === 1) await firstExecutionGate;
      if (executionMarkers.length === 2) await secondExecutionGate;
      return result(marker, audio.length / 16_000);
    } finally {
      release(observer);
    }
  };

  const telemetry = createAlloySttTelemetryTracker({
    meetingId: "502",
    nativeMeetingId: "alloy-limiter-observer-room",
  });
  const observedSink: TranscriptSink = {
    segment: () => {},
    draft: () => {},
    finalize: () => {},
  };
  const pipe = createGmeetPipeline({
    transcribe: observedTranscribe,
    sink: observedSink,
    serializeTranscriptionByChannel: true,
    alloySttTelemetry: telemetry,
    config: {
      minAudioDuration: 0.01,
      submitInterval: 0.01,
      confirmThreshold: 1,
    },
  });

  try {
    pipe.feedAudio(0, "Alpha", pcm(0.1), 0);
    await waitFor(
      () => executionMarkers.length === 1 || missingObserver,
      "the first observer-owned execution",
    );
    if (missingObserver) throw new Error("expected gmeet to forward the limiter observer");

    pipe.feedAudio(1, "Beta", pcm(0.2), 0);
    await waitFor(
      () => telemetry.snapshot().queued_audio_sec >= 0.025 || missingObserver,
      "the cross-channel limiter waiter",
    );
    if (missingObserver) throw new Error("expected gmeet to forward the limiter observer");

    pipe.feedAudio(1, "Gamma", pcm(0.3), 30);
    await waitFor(
      () => telemetry.snapshot().queued_audio_sec >= 0.05,
      "the same-channel scheduler-pending request",
    );
    await sleep(75);

    let snapshot = telemetry.snapshot();
    if (
      snapshot.active_requests !== 1
      || snapshot.waiting_channels !== 1
      || Math.abs(snapshot.queued_audio_sec - 0.05) > 1e-9
    ) {
      throw new Error(
        `expected active A plus limiter B and pending C on one waiting channel; `
        + `active=${snapshot.active_requests} waiting=${snapshot.waiting_channels} `
        + `queued=${snapshot.queued_audio_sec}`,
      );
    }

    releaseFirstExecution();
    await waitFor(() => executionMarkers.includes(2), "the limiter waiter to start");
    await waitFor(() => telemetry.snapshot().processed_windows >= 1, "the first completion");
    snapshot = telemetry.snapshot();
    if (snapshot.active_requests !== 1 || snapshot.waiting_channels < 1) {
      throw new Error(
        `expected one slot holder and at least the same-channel pending request; `
        + `active=${snapshot.active_requests} waiting=${snapshot.waiting_channels}`,
      );
    }
    if (snapshot.rtf_ema === null || Math.abs(snapshot.rtf_ema - 0.2) > 1e-9) {
      throw new Error(
        `expected RTF 0.2 from literal 5ms slot hold over 25ms audio, got ${snapshot.rtf_ema}`,
      );
    }

    releaseSecondExecution();
    await pipe.dispose();
    snapshot = telemetry.snapshot();
    if (snapshot.active_requests !== 0 || snapshot.waiting_channels !== 0) {
      throw new Error(
        `expected observer telemetry to drain; active=${snapshot.active_requests} `
        + `waiting=${snapshot.waiting_channels}`,
      );
    }
  } finally {
    releaseFirstExecution?.();
    releaseSecondExecution?.();
  }

  console.log("PASS ALLOY limiter observer owns active/waiting transitions and queue-free RTF");
}

async function run() {
  await runRotationRegression();
  await runLimiterObserverTelemetry();
}

run().catch((error) => {
  releaseFirst?.();
  console.error(error);
  process.exit(1);
});
