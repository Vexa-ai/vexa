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
export { createGmeetPipeline } from './gmeet-pipeline';
export type { GmeetPipeline, GmeetPipelineOptions } from './gmeet-pipeline';
export { SpeakerStreamManager } from './speaker-streams';
export type { SpeakerStreamManagerConfig } from './speaker-streams';
export { isHallucination } from './hallucination-filter';
export { setLogger } from './log';
export type { TranscriptSegment, TranscriptSink, TimestampedWord, TranscriptMeta } from './contracts/transcript-v1';
