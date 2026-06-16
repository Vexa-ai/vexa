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
export { SileroVAD } from "./vad";
export type { VadSpeakerState } from "./vad";
export { isHallucination } from "./hallucination-filter";
export { TranscriptionClient } from "@vexa/transcribe-whisper";
export type { TranscriptionWord, TranscriptionSegment, TranscriptionResult, TranscriptionClientConfig } from "@vexa/transcribe-whisper";
export { setLogger } from "./log";
// The MIXED lane (zoom/teams) moved out to @vexa/mixed-pipeline (segmenter +
// hints-namer, no clustering). The diarizer monolith + wespeaker/online-clustering
// + the createMixedPipeline/separated-transcript.v1 adapter are dropped (per plan).

// The gmeet CHANNEL-routed strategy: capture.v1 (channel + glow name) → transcript.v1.
// Overlap-safe — separate channels transcribe independently; glow names each turn,
// bound at onset and held through overlap. No diarizer, no post-hoc attribution.
export { createGmeetPipeline } from "./gmeet-pipeline";
export type { GmeetPipeline, GmeetPipelineOptions } from "./gmeet-pipeline";
