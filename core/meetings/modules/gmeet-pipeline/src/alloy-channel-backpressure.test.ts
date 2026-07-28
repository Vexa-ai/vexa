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
  SpeakerStreamManager,
  type AlloySttTelemetryTracker,
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

async function runCleanupDeadlineRotationRegression() {
  // ALLOY: advance only the production turn-cleanup deadline. Every other timer
  // keeps its real semantics, and the sole controlled dependency is the first STT promise.
  const realSetTimeout = globalThis.setTimeout;
  const cleanupCallbacks: Array<() => void> = [];
  let cleanupTimerCount = 0;
  globalThis.setTimeout = ((
    callback: (...args: any[]) => void,
    delay?: number,
    ...args: any[]
  ) => {
    if (delay === 12_000) {
      cleanupTimerCount++;
      cleanupCallbacks.push(() => callback(...args));
      return { unref: () => undefined } as unknown as ReturnType<typeof setTimeout>;
    }
    return realSetTimeout(callback, delay, ...args);
  }) as typeof setTimeout;

  // ALLOY: observe the real manager lifecycle without adding a production-only
  // inspection API. The original methods still perform every state transition.
  const realAddSpeaker = SpeakerStreamManager.prototype.addSpeaker;
  const realRemoveSpeaker = SpeakerStreamManager.prototype.removeSpeaker;
  const retainedSpeakers = new Set<string>();
  SpeakerStreamManager.prototype.addSpeaker = function (
    speakerId: string,
    speakerName: string,
  ): void {
    realAddSpeaker.call(this, speakerId, speakerName);
    if (this.hasSpeaker(speakerId)) retainedSpeakers.add(speakerId);
  };
  SpeakerStreamManager.prototype.removeSpeaker = function (speakerId: string): void {
    realRemoveSpeaker.call(this, speakerId);
    if (!this.hasSpeaker(speakerId)) retainedSpeakers.delete(speakerId);
  };

  let releaseHeld!: () => void;
  const held = new Promise<void>((resolve) => { releaseHeld = resolve; });
  const calls: number[] = [];
  let activeCalls = 0;
  let peakCalls = 0;
  const rows = new Map<string, Parameters<TranscriptSink["segment"]>[0]>();
  const telemetry = createAlloySttTelemetryTracker({
    meetingId: "503",
    nativeMeetingId: "alloy-cleanup-deadline-room",
  });
  const deadlineSink: TranscriptSink = {
    segment: (segment) => { rows.set(segment.segment_id, segment); },
    draft: (segment) => {
      if (segment.text.trim()) rows.set(segment.segment_id, segment);
      else rows.delete(segment.segment_id);
    },
    finalize: () => {},
  };
  const heldTranscribe = async (
    audio: Float32Array,
    _prompt?: string,
    observer?: TranscriptionExecutionObserver,
  ): Promise<TranscriptionResult> => {
    const marker = Math.round(audio[audio.length - 1] * 10);
    calls.push(marker);
    activeCalls++;
    peakCalls = Math.max(peakCalls, activeCalls);
    observer?.started();
    try {
      if (calls.length === 1) await held;
      return result(marker, audio.length / 16_000);
    } finally {
      observer?.finished(5);
      activeCalls--;
    }
  };
  const pipe = createGmeetPipeline({
    transcribe: heldTranscribe,
    sink: deadlineSink,
    serializeTranscriptionByChannel: true,
    alloySttTelemetry: telemetry,
    config: {
      minAudioDuration: 0.01,
      submitInterval: 0.01,
      confirmThreshold: 2,
    },
  });
  let disposed = false;

  try {
    pipe.feedAudio(0, "Alice", pcm(0.1), 0);
    await waitFor(() => calls.length === 1, "the held Alice STT request");

    pipe.feedAudio(0, "Bob", pcm(0.2), 30);
    await waitFor(
      () => retainedSpeakers.has("ch-0:1") && retainedSpeakers.has("ch-0:2"),
      "both rotated speaker buffers before the cleanup deadline",
    );

    // ALLOY: advance any captured legacy cleanup deadline. Serialized scheduling
    // intentionally captures none, so scheduler-owned audio stays available.
    for (const cleanup of cleanupCallbacks.splice(0)) cleanup();
    releaseHeld();

    await waitFor(
      () => calls.includes(2),
      "Bob after the held Alice request",
    );
    await waitFor(
      () => [...rows.values()].some((segment) => segment.speaker === "Bob"),
      "Bob's draft before dispose",
    );
    await pipe.dispose();
    disposed = true;

    const completed = [...rows.values()]
      .filter((segment) => segment.completed)
      .map((segment) => segment.speaker);
    const danglingDrafts = [...rows.values()].filter((segment) => !segment.completed);
    if (completed.join(",") !== "Alice,Bob") {
      throw new Error(
        `cleanup deadline lost or duplicated a turn: completed=${completed.join(",") || "<none>"} `
        + `calls=${calls.join(",")} cleanup_timers=${cleanupTimerCount}`,
      );
    }
    if (calls.join(",") !== "1,1,2") {
      throw new Error(
        `expected Alice active+final before Bob after the cleanup deadline; calls=${calls.join(",")}`,
      );
    }
    if (danglingDrafts.length !== 0) {
      throw new Error(`expected no dangling drafts, got ${JSON.stringify(danglingDrafts)}`);
    }
    if (peakCalls !== 1) {
      throw new Error(`expected peak STT concurrency 1, got ${peakCalls}`);
    }
    const drained = telemetry.snapshot();
    if (drained.active_requests !== 0 || drained.waiting_channels !== 0) {
      throw new Error(
        `expected drained telemetry; active=${drained.active_requests} `
        + `waiting=${drained.waiting_channels}`,
      );
    }
    if (retainedSpeakers.size !== 0) {
      throw new Error(`expected no retained speakers, got ${[...retainedSpeakers].join(",")}`);
    }
    if (cleanupTimerCount !== 0) {
      throw new Error(
        `serialized turns must be scheduler-cleaned, got ${cleanupTimerCount} legacy cleanup timer(s)`,
      );
    }
  } finally {
    releaseHeld?.();
    if (!disposed) await pipe.dispose().catch(() => undefined);
    SpeakerStreamManager.prototype.addSpeaker = realAddSpeaker;
    SpeakerStreamManager.prototype.removeSpeaker = realRemoveSpeaker;
    globalThis.setTimeout = realSetTimeout;
  }

  console.log("PASS ALLOY scheduler owns closed-turn cleanup beyond the legacy 12s deadline");
}

async function runUpstreamCleanupNegativeControl() {
  // ALLOY: disabled serialization must keep Vexa's literal 12s cleanup behavior.
  const realSetTimeout = globalThis.setTimeout;
  const cleanupCallbacks: Array<() => void> = [];
  let cleanupTimerCount = 0;
  globalThis.setTimeout = ((
    callback: (...args: any[]) => void,
    delay?: number,
    ...args: any[]
  ) => {
    if (delay === 12_000) {
      cleanupTimerCount++;
      cleanupCallbacks.push(() => callback(...args));
      return { unref: () => undefined } as unknown as ReturnType<typeof setTimeout>;
    }
    return realSetTimeout(callback, delay, ...args);
  }) as typeof setTimeout;

  let calls = 0;
  const pipe = createGmeetPipeline({
    transcribe: async (audio) => {
      calls++;
      return result(Math.round(audio[audio.length - 1] * 10), audio.length / 16_000);
    },
    sink: { segment: () => {}, finalize: () => {} },
    serializeTranscriptionByChannel: false,
    config: {
      minAudioDuration: 0.01,
      submitInterval: 0.01,
      confirmThreshold: 1,
    },
  });
  let disposed = false;

  try {
    pipe.feedAudio(0, "Alice", pcm(0.1), 0);
    await waitFor(() => calls === 1, "the upstream Alice request");
    pipe.feedAudio(0, "Bob", pcm(0.2), 30);
    if (cleanupTimerCount !== 1) {
      throw new Error(`expected one upstream 12s cleanup timer, got ${cleanupTimerCount}`);
    }
    for (const cleanup of cleanupCallbacks.splice(0)) cleanup();
    await pipe.dispose();
    disposed = true;
  } finally {
    if (!disposed) await pipe.dispose().catch(() => undefined);
    globalThis.setTimeout = realSetTimeout;
  }

  console.log("PASS disabled ALLOY serialization preserves upstream 12s cleanup");
}

async function runThrowingTelemetryIsolation() {
  // ALLOY: every optional diagnostic callback records its real state transition,
  // then throws. None may change audio, STT scheduling, failure handling, or sink output.
  const backing = createAlloySttTelemetryTracker({
    meetingId: "504",
    nativeMeetingId: "alloy-throwing-telemetry-room",
  });
  const observedEvents: string[] = [];
  const explode = (event: string): never => {
    observedEvents.push(event);
    throw new Error(`telemetry ${event} exploded`);
  };
  const throwingTelemetry: AlloySttTelemetryTracker = {
    captured(channelId, audioEndMs) {
      backing.captured(channelId, audioEndMs);
      explode("captured");
    },
    queued(requestId, channelId, audioSec) {
      backing.queued(requestId, channelId, audioSec);
      explode("queued");
    },
    superseded(requestId) {
      backing.superseded(requestId);
      explode("superseded");
    },
    started(requestId, channelId, audioSec) {
      backing.started(requestId, channelId, audioSec);
      explode("started");
    },
    finished(requestId) {
      backing.finished(requestId);
      explode("finished");
    },
    completed(input) {
      backing.completed(input);
      explode("completed");
    },
    failed(requestId, error) {
      backing.failed(requestId, error);
      explode("failed");
    },
    recovered() {
      backing.recovered();
      explode("recovered");
    },
    snapshot: () => backing.snapshot(),
  };

  let releaseFirstFailure!: () => void;
  const firstFailureGate = new Promise<void>((resolve) => { releaseFirstFailure = resolve; });
  const calls: number[] = [];
  let activeCalls = 0;
  let peakCalls = 0;
  let surfacedSttFailures = 0;
  const completed: string[] = [];
  const pipe = createGmeetPipeline({
    transcribe: async (audio, _prompt, observer) => {
      const marker = Math.round(audio[audio.length - 1] * 10);
      calls.push(marker);
      activeCalls++;
      peakCalls = Math.max(peakCalls, activeCalls);
      observer?.started();
      try {
        if (calls.length === 1) {
          await firstFailureGate;
          throw new Error("controlled STT failure");
        }
        return result(marker, audio.length / 16_000);
      } finally {
        observer?.finished(5);
        activeCalls--;
      }
    },
    sink: {
      segment: (segment) => { completed.push(segment.speaker); },
      draft: () => {},
      finalize: () => {},
    },
    onError: () => { surfacedSttFailures++; },
    serializeTranscriptionByChannel: true,
    alloySttTelemetry: throwingTelemetry,
    config: {
      minAudioDuration: 0.01,
      submitInterval: 0.01,
      confirmThreshold: 1,
    },
  });
  let disposed = false;

  try {
    pipe.feedAudio(0, "Alice", pcm(0.1), 0);
    await waitFor(() => calls.length === 1, "Alice before the controlled STT failure");
    pipe.feedAudio(0, "Bob", pcm(0.2), 30);
    await waitFor(
      () => backing.snapshot().waiting_channels === 1,
      "Bob queued behind Alice",
    );
    pipe.feedAudio(0, "Carol", pcm(0.3), 60);
    await waitFor(
      () => backing.snapshot().superseded_windows === 1,
      "Carol superseding Bob",
    );
    releaseFirstFailure();

    await waitFor(
      () => completed.includes("Alice") && completed.includes("Carol"),
      "Alice finalization and latest Carol turn",
    );
    await pipe.dispose();
    disposed = true;

    if (completed.join(",") !== "Alice,Carol") {
      throw new Error(`expected Alice,Carol once after telemetry faults; got ${completed.join(",")}`);
    }
    if (calls.join(",") !== "1,1,3") {
      throw new Error(`expected failed Alice, final Alice, latest Carol; calls=${calls.join(",")}`);
    }
    if (surfacedSttFailures !== 1) {
      throw new Error(`expected one real STT fault at onError, got ${surfacedSttFailures}`);
    }
    if (peakCalls !== 1) {
      throw new Error(`telemetry faults changed STT concurrency; peak=${peakCalls}`);
    }
    for (const event of [
      "captured",
      "queued",
      "started",
      "finished",
      "completed",
      "recovered",
      "failed",
      "superseded",
    ]) {
      if (!observedEvents.includes(event)) {
        throw new Error(`throwing telemetry callback was not exercised: ${event}`);
      }
    }
    const drained = backing.snapshot();
    if (drained.active_requests !== 0 || drained.waiting_channels !== 0) {
      throw new Error(
        `throwing telemetry did not drain; active=${drained.active_requests} `
        + `waiting=${drained.waiting_channels}`,
      );
    }
  } finally {
    releaseFirstFailure?.();
    if (!disposed) await pipe.dispose().catch(() => undefined);
  }

  console.log("PASS ALLOY telemetry callbacks are best-effort across success, failure, and supersession");
}

async function runTelemetryAudioEndStateLifecycle() {
  // ALLOY: instrument Map construction/state inside the real pipeline so this
  // memory-only requirement needs no production inspection surface.
  const RealMap = globalThis.Map;
  let phase: "disabled" | "enabled" = "disabled";
  let allocations = 0;
  let disabledAudioEndWrites = 0;
  const enabledAudioEndMaps = new Set<Map<unknown, unknown>>();
  class ObservedMap<K, V> extends Map<K, V> {
    private readonly observedAllocation = (() => {
      allocations++;
      return true;
    })();

    override set(key: K, value: V): this {
      if (
        typeof key === "string"
        && /^ch-\d+:\d+$/.test(key)
        && typeof value === "number"
        && (value === 25 || value === 55)
      ) {
        if (phase === "disabled") disabledAudioEndWrites++;
        else enabledAudioEndMaps.add(this as unknown as Map<unknown, unknown>);
      }
      return super.set(key, value);
    }
  }

  const telemetry = createAlloySttTelemetryTracker({
    meetingId: "505",
    nativeMeetingId: "alloy-audio-end-state-room",
  });
  globalThis.Map = ObservedMap as MapConstructor;

  let disabledPipe: ReturnType<typeof createGmeetPipeline> | undefined;
  let enabledPipe: ReturnType<typeof createGmeetPipeline> | undefined;
  try {
    let disabledCalls = 0;
    const disabledBefore = allocations;
    disabledPipe = createGmeetPipeline({
      transcribe: async (audio) => {
        disabledCalls++;
        return result(1, audio.length / 16_000);
      },
      sink: { segment: () => {}, finalize: () => {} },
      serializeTranscriptionByChannel: true,
      config: {
        minAudioDuration: 0.01,
        submitInterval: 0.01,
        confirmThreshold: 1,
      },
    });
    const disabledAllocations = allocations - disabledBefore;
    disabledPipe.feedAudio(0, "NoTelemetry", pcm(0.1), 0);
    await waitFor(() => disabledCalls === 1, "the no-telemetry STT request");
    await disabledPipe.dispose();
    disabledPipe = undefined;

    phase = "enabled";
    const enabledCompleted: string[] = [];
    const enabledBefore = allocations;
    enabledPipe = createGmeetPipeline({
      transcribe: async (audio, _prompt, observer) => {
        observer?.started();
        try {
          return result(Math.round(audio[audio.length - 1] * 10), audio.length / 16_000);
        } finally {
          observer?.finished(5);
        }
      },
      sink: {
        segment: (segment) => { enabledCompleted.push(segment.speaker); },
        finalize: () => {},
      },
      serializeTranscriptionByChannel: true,
      alloySttTelemetry: telemetry,
      config: {
        minAudioDuration: 0.01,
        submitInterval: 0.01,
        confirmThreshold: 1,
      },
    });
    const enabledAllocations = allocations - enabledBefore;
    enabledPipe.feedAudio(0, "Alice", pcm(0.1), 0);
    await waitFor(() => enabledCompleted.includes("Alice"), "enabled Alice completion");
    enabledPipe.feedAudio(0, "Bob", pcm(0.2), 30);
    await waitFor(() => enabledCompleted.includes("Bob"), "enabled Bob completion");

    const audioEndMap = [...enabledAudioEndMaps].find((map) => map.has("ch-0:2"));
    const failures: string[] = [];
    if (enabledAllocations !== disabledAllocations + 1) {
      failures.push(
        `telemetry-only Map allocation mismatch: disabled=${disabledAllocations} `
        + `enabled=${enabledAllocations}`,
      );
    }
    if (disabledAudioEndWrites !== 0) {
      failures.push(`telemetry-off wrote ${disabledAudioEndWrites} audio-end entr${disabledAudioEndWrites === 1 ? "y" : "ies"}`);
    }
    if (!audioEndMap) {
      failures.push("telemetry-on audio-end Map was not observed");
    } else if (audioEndMap.has("ch-0:1")) {
      failures.push("released Alice audio-end entry remained retained");
    }

    await enabledPipe.dispose();
    enabledPipe = undefined;
    if (audioEndMap && audioEndMap.size !== 0) {
      failures.push(`dispose retained ${audioEndMap.size} audio-end entr${audioEndMap.size === 1 ? "y" : "ies"}`);
    }
    if (failures.length) throw new Error(failures.join("; "));
  } finally {
    await disabledPipe?.dispose().catch(() => undefined);
    await enabledPipe?.dispose().catch(() => undefined);
    globalThis.Map = RealMap;
  }

  console.log("PASS ALLOY audio-end state is telemetry-only and released with each turn");
}

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

async function runSameSpeakerCodeSwitchContinuityRegression() {
  let releaseHeld!: () => void;
  const held = new Promise<void>((resolve) => { releaseHeld = resolve; });
  const requests: number[][] = [];
  let activeCalls = 0;
  let peakCalls = 0;
  const pipe = createGmeetPipeline({
    transcribe: async (audio) => {
      const markers = [...new Set(
        Array.from(audio, (sample) => Math.round(sample * 10)),
      )];
      requests.push(markers);
      activeCalls++;
      peakCalls = Math.max(peakCalls, activeCalls);
      try {
        if (requests.length === 1) await held;
        return result(markers[markers.length - 1] ?? 0, audio.length / 16_000);
      } finally {
        activeCalls--;
      }
    },
    sink: {
      segment: () => {},
      draft: () => {},
      finalize: () => {},
    },
    serializeTranscriptionByChannel: true,
    config: {
      minAudioDuration: 0.01,
      submitInterval: 0.01,
      confirmThreshold: 1,
    },
  });

  try {
    pipe.feedAudio(0, "Code Switch Guest", pcm(0.1), 0);
    await waitFor(() => requests.length === 1, "the held first language window");

    // The same remote participant resumes after two capture-visible gaps while
    // the first CPU STT request is still active. Every language window remains
    // product audio: backpressure may coalesce it, but must not discard it.
    pipe.feedAudio(0, "Code Switch Guest", pcm(0.2), 1_500);
    await sleep(20);
    pipe.feedAudio(0, "Code Switch Guest", pcm(0.3), 3_000);
    await sleep(20);
    releaseHeld();

    await waitFor(
      () => {
        const observed = new Set(requests.flat());
        return observed.has(1) && observed.has(2) && observed.has(3);
      },
      "all EN-RU-EN capture markers to reach STT",
    );
    await pipe.dispose();

    if (peakCalls !== 1) {
      throw new Error(`same channel dispatched ${peakCalls} concurrent STT requests`);
    }
  } finally {
    releaseHeld?.();
    await pipe.dispose().catch(() => undefined);
  }

  console.log("PASS ALLOY same-speaker gaps preserve every code-switch audio window");
}

async function run() {
  await runCleanupDeadlineRotationRegression();
  await runUpstreamCleanupNegativeControl();
  await runThrowingTelemetryIsolation();
  await runTelemetryAudioEndStateLifecycle();
  await runRotationRegression();
  await runLimiterObserverTelemetry();
  await runSameSpeakerCodeSwitchContinuityRegression();
}

run().catch((error) => {
  releaseFirst?.();
  console.error(error);
  process.exit(1);
});
