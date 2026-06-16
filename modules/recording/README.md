# @vexa/recording — the meeting media-file recorder

Records the meeting itself — audio, and optionally video — to a media file: the
artifact a user plays back later. This is **not the same as [`../recorder`](../recorder/)**:
`recorder` captures the `capture.v1` *debug fixture* (frames + hints, to replay
the pipeline offline); `recording` produces the *deliverable recording* of the call.

`UnifiedRecordingPipeline` drives an `AudioCaptureSource` into an injected
`ChunkSink`. Two capture sources ship with the brick:

- `PulseAudioCapture` — a `parec` child process (server / container audio).
- `MediaRecorderCapture` — in-page capture via Playwright.

**Every host coupling is injected — the brick never imports the bot:**
- `ChunkSink` — the host's recording service satisfies it (`uploadChunk`).
- `SessionStartSink` via `setSessionStartProvider` — supplies the session clock.
- `setLoggers({ log, logJSON })` — structured logging stays host-shaped.

`VideoRecordingService` (HW-accelerated) is also exported for hosts that record
the screen alongside the audio.

## Surface
`UnifiedRecordingPipeline`, `PulseAudioCapture`, `MediaRecorderCapture`,
`RecordingService`, `VideoRecordingService`; the ports `AudioCaptureSource` /
`ChunkSink` / `SessionStartSink`; `setSessionStartProvider`, `setLoggers`.

## Gates
`npm run check:isolation` · `npm run build`.
