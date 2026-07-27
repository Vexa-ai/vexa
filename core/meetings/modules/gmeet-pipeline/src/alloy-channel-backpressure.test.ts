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
import type { TranscriptionResult } from "@vexa/transcribe-whisper";

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const pcm = (marker: number) => new Float32Array(400).fill(marker);

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

run().catch((error) => {
  releaseFirst?.();
  console.error(error);
  process.exit(1);
});
