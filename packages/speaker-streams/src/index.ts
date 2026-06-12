/**
 * @vexa/speaker-streams — the pipeline core brick (MANIFEST domain row).
 * Consumes capture.v1 audio (feedAudio) + speaker events; emits attributed,
 * confirmed segments. stt.v1 egress via TranscriptionClient. Replay-proven:
 * the fixture-replay harness reproduces a live transcript from a recorded
 * capture.v1 fixture through exactly this surface.
 * Contract note: addSpeaker() MUST precede feedAudio (it arms the submit timer).
 */
export { SpeakerStreamManager } from "./speaker-streams";
export type { SpeakerStreamManagerConfig } from "./speaker-streams";
export { mapWordsToSpeakers, captionsToSpeakerBoundaries } from "./speaker-mapper";
export type { TimestampedWord, SpeakerBoundary, AttributedSegment, CaptionEvent } from "./speaker-mapper";
export { SileroVAD } from "./vad";
export type { VadSpeakerState } from "./vad";
export { isHallucination } from "./hallucination-filter";
export { TranscriptionClient } from "./transcription-client";
export type { TranscriptionWord, TranscriptionSegment, TranscriptionResult, TranscriptionClientConfig } from "./transcription-client";
export { setLogger } from "./log";
