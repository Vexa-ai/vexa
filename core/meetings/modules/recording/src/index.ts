/**
 * @vexa/recording — the recording brick (emits recording.v1).
 *
 * One concern, two halves that always work together:
 *  - ACQUIRE: AudioCaptureSource (PulseAudio parec | in-page MediaRecorder) +
 *    UnifiedRecordingPipeline drive raw meeting audio/video into a ChunkSink.
 *  - DELIVER: RecordingService / VideoRecordingService chunk-upload over HTTP
 *    to the server-side receiver (meeting-api), which assembles the final file.
 *
 * Internal strategy axes (NOT separate bricks): MediaRecorder (gmeet/teams) vs
 * PulseAudio (zoom); audio vs video (x11grab). All host couplings injected —
 * the brick never imports the bot (one-way rule).
 *
 * Contract: recording.v1 (chunk_seq + is_final + format → meeting-api).
 */
export * from "./audio-pipeline";          // UnifiedRecordingPipeline, MediaRecorderCapture, PulseAudioCapture, ChunkSink
export { RecordingService } from "./recording";
export { VideoRecordingService } from "./video-recording";
export { setLoggers } from "./log";
export { buildRecordingMaster } from "./recording-codec";   // recording.v1 master codec; Python twin = meeting-api/recording_codec.py
// recording.v1 FINALIZE LIFECYCLE (accumulate-by-seq → drop empty final → build on is_final OR close).
// Owns the stateful part around buildRecordingMaster; the desktop receiver reuses it. Python twin = meeting-api finalizer.
export { createRecordingAssembler } from "./recording-assembler";
export type { RecordingAssembler, RecordingAssemblerOptions, RecordingMaster, RecordingMasterFormat } from "./recording-assembler";
