/**
 * @vexa/recording — the media-chunk delivery brick (recording.v1, bot side).
 * Streams recorded chunks (seq + is_final + format) to the server-side
 * receiver (meeting-api internal_upload_recording), which assembles the
 * final media file into storage. RecordingService satisfies the ChunkSink
 * shape that @vexa/audio-pipelines takes by injection.
 */
export { RecordingService } from "./recording";
export { VideoRecordingService } from "./video-recording";
export { setLoggers } from "./log";
