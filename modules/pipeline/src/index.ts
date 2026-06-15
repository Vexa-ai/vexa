/**
 * @vexa/pipeline — the pipeline core brick (MANIFEST domain row).
 * Consumes capture.v1 audio (feedAudio) + speaker events; emits attributed,
 * confirmed segments. stt.v1 egress via TranscriptionClient. Replay-proven:
 * the fixture-replay harness reproduces a live transcript from a recorded
 * capture.v1 fixture through exactly this surface.
 * Contract note: addSpeaker() MUST precede feedAudio (it arms the submit timer).
 */
export { SpeakerStreamManager } from "./speaker-streams";
export type { SpeakerStreamManagerConfig } from "./speaker-streams";
// Speaker attribution (mapWordsToSpeakers / captionsToSpeakerBoundaries) moved
// to @vexa/speaker-attribution — it consumes this brick's separated-transcript.v1.
export { SileroVAD } from "./vad";
export type { VadSpeakerState } from "./vad";
export { isHallucination } from "./hallucination-filter";
export { TranscriptionClient } from "./transcription-client";
export type { TranscriptionWord, TranscriptionSegment, TranscriptionResult, TranscriptionClientConfig } from "./transcription-client";
export { setLogger } from "./log";
// Mixed-topology strategy (zoom/teams): single-pass streaming core —
// gate (pyannote segmentation) cuts turns, diarizer (wespeaker + online
// clustering) labels them with opaque cluster ids, Whisper transcribes.
// Ported verbatim from the bot (services/vexa-bot/core/src/services).
export { ChunkedTranscriber } from "./chunked-transcriber";
export type { ChunkedTranscriberCallbacks, ChunkSegment } from "./chunked-transcriber";
export { OnnxLocalDiarizer } from "./diarization/onnx-local-diarizer";
export type { CommitEvent } from "./diarization/onnx-local-diarizer";
export { ClusterNameBinder } from "./cluster-name-binder";
export type { HintKind } from "./cluster-name-binder";
// The MIXED-topology strategy as a contract adapter: capture.v1 → separated-transcript.v1
// (opaque cluster ids; naming is the downstream speaker-attribution brick).
export { createMixedPipeline } from "./mixed-pipeline";
export type { MixedPipeline, MixedPipelineOptions } from "./mixed-pipeline";

// The gmeet CHANNEL-routed strategy: capture.v1 (channel + glow name) → transcript.v1.
// Overlap-safe — separate channels transcribe independently; glow names each turn,
// bound at onset and held through overlap. No diarizer, no post-hoc attribution.
export { createGmeetPipeline } from "./gmeet-pipeline";
export type { GmeetPipeline, GmeetPipelineOptions } from "./gmeet-pipeline";
