# recording/src

Front door [`index.ts`](index.ts). The pieces:

- [`audio-pipeline.ts`](audio-pipeline.ts) — `AudioCaptureSource` (`PulseAudioCapture`,
  `MediaRecorderCapture`) + `UnifiedRecordingPipeline` (drives capture → `ChunkSink.uploadChunk`,
  marks the final chunk `isFinal`, single error path, segment↔audio `started` alignment hook).
- [`recording.ts`](recording.ts) — `RecordingService`: WAV accumulation + chunked/whole HTTP multipart
  upload to meeting-api (retry w/ backoff). `uploadChunk` is the bot/prod recording.v1 transport.
- [`video-recording.ts`](video-recording.ts) — `VideoRecordingService`: ffmpeg `x11grab` of the Xvfb
  display (`none`/`vaapi`/`nvenc`), upload, audio mux.
- [`recording-codec.ts`](recording-codec.ts) — `buildRecordingMaster`: the recording.v1 MASTER codec
  (WebM byte-concat / WAV RIFF-merge). Twin of meeting-api `recording_codec.py`, pinned to
  [`contracts/golden/`](contracts/golden/).
- [`log.ts`](log.ts) — host-injectable `log` / `logJSON` (`setLoggers`).

External imports: `playwright` (the only dep) + Node builtins. Host couplings (chunk sink, loggers,
session-start provider) are injected, never imported — the one-way rule. Tests:
[`golden.test.ts`](golden.test.ts), [`assemble.smoke.test.ts`](assemble.smoke.test.ts).
