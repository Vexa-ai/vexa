/**
 * @vexa/audio-pipelines — the multichannel topology brick.
 * UnifiedRecordingPipeline drives an AudioCaptureSource (PulseAudio parec or
 * in-page MediaRecorder) into an injected ChunkSink; SessionStartSink and
 * loggers are injected too — the brick never imports the bot (one-way rule).
 */
export * from "./audio-pipeline";
export { setLoggers } from "./log";
