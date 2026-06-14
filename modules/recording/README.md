# audio-pipelines — the multichannel topology brick

`UnifiedRecordingPipeline` drives an `AudioCaptureSource` into an injected
`ChunkSink`. Two capture sources ship with the brick: `PulseAudioCapture`
(parec child process) and `MediaRecorderCapture` (in-page via Playwright).

**Every host coupling is injected — the brick never imports the bot:**
- `ChunkSink` — the bot's RecordingService satisfies it (`uploadChunk`).
- `SessionStartSink` via `setSessionStartProvider` — the bot's SegmentPublisher.
- `setLoggers({log, logJSON})` — structured logging stays host-shaped.

The mixed/chunked topology (chunked-host + chunked-transcriber + diarization)
remains in the bot — it extracts with the diarization and delivery bricks.

Gates: `npm run check:isolation` · `npm run build`.
