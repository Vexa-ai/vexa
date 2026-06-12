# recording — the media-chunk delivery brick (recording.v1, bot side)

Streams recorded media chunks (seq + `is_final` + format) over HTTP to the
server-side receiver (meeting-api `internal_upload_recording`, token-gated),
which assembles the final media file into S3/MinIO. See `contracts/recording/v1/`.

- `src/recording.ts` — `RecordingService`: audio accumulation (local WAV) +
  chunked upload. Satisfies the `ChunkSink` shape that `@vexa/audio-pipelines`
  takes by injection — the pipeline never imports this brick.
- `src/video-recording.ts` — `VideoRecordingService`: Xvfb capture via ffmpeg
  x11grab + the same chunked upload path.
- `src/log.ts` — host-injectable `setLoggers({log, logJSON})`.

Harness (planned): replay a recorded chunk sequence against a fake receiver;
oracle = assembled file byte-equal to the original media.

Gates: `npm run check:isolation` · `npm run build`.
