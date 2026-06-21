/**
 * @vexa/gmeet-pipeline — the GMEET lane (channel-routed).
 *
 *   gmeet-capture.v1 (named per-channel audio) ─► channel router
 *        ├─ per-channel sliding-window buffer + LocalAgreement confirm (shared engine)
 *        └─ shared/whisper stt.v1 transcribe (injected)
 *   ─► transcript.v1 (named segments)
 *
 * Overlap-safe: each participant channel transcribes independently and the speaker
 * name is bound at capture (glow↔channel), so there is NO downstream namer and no
 * diarization. Contrast @vexa/mixed-pipeline (one mixed stream, names from hints).
 */
export { createGmeetPipeline } from './gmeet-pipeline.js';
export type { GmeetPipeline, GmeetPipelineOptions } from './gmeet-pipeline.js';
export { SpeakerStreamManager } from './speaker-streams.js';
export type { SpeakerStreamManagerConfig } from './speaker-streams.js';
export { isHallucination } from './hallucination-filter.js';
export { setLogger } from './log.js';
export type { TranscriptSegment, TranscriptSink, TimestampedWord, TranscriptMeta, Source } from './contracts/transcript-v1.js';
