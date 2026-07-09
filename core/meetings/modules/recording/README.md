# @vexa/recording — the recording brick (emits `recording.v1`)

_meetings/ · module · acquire (PulseAudio / MediaRecorder) + deliver (chunked upload) + Node master codec._

One concern, two halves that always work together:

- **ACQUIRE** — `AudioCaptureSource` (`PulseAudioCapture` = Zoom's `parecord` subprocess; `MediaRecorderCapture`
  = the GMeet/Teams browser-MediaRecorder bridge over a Playwright `Page`) + `UnifiedRecordingPipeline`
  drive raw meeting audio into a `ChunkSink`. `VideoRecordingService` captures the Xvfb display via
  ffmpeg `x11grab`.
- **DELIVER** — `RecordingService` / `VideoRecordingService` chunk-upload over HTTP multipart to the
  server-side receiver (meeting-api `/internal/recordings/upload`), which assembles the final file.

Internal strategy axes (NOT separate bricks): MediaRecorder (gmeet/teams) vs PulseAudio (zoom); audio
vs video (x11grab). All host couplings are **injected** — the brick never imports the bot (one-way rule).
The `ChunkSink` and the loggers (`setLoggers`) are handed in.

`buildRecordingMaster(format, chunks)` is the **`recording.v1` MASTER codec** (Node): byte-concat for
WebM, RIFF-aware header-merge for WAV. It is the twin of meeting-api's `recording_codec.py`, pinned to
the SAME golden vectors (`src/contracts/golden/`) — used by the all-Node desktop, which has no meeting-api.

> recording.v1 has TWO transports by deployment (deliberate): the bot/prod path is HTTP multipart
> (`RecordingService.uploadChunk`); the desktop path is the same `recording.v1` chunk over the ingest WS,
> encoded by [`@vexa/capture-codec`](../capture-codec/) (`encodeRecordingChunk`). Same `{seq, is_final,
> format, bytes}` contract, two wires.

## Surface
`UnifiedRecordingPipeline` · `PulseAudioCapture` · `MediaRecorderCapture` · `RecordingService` ·
`VideoRecordingService` · `buildRecordingMaster` · `setLoggers` · `setSessionStartProvider`
(+ types `AudioChunk`, `AudioCaptureSource`, `ChunkSink`, `SessionStartSink`, `VideoHwAccel`, …).
Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/recording run build` — `tsc` clean (self-contained CommonJS `tsconfig`: this is a
Node brick — `__dirname`, extensionless relative imports, value-imports of `playwright` types — so it
does NOT extend the ESM `tsconfig.base`). Tests (`pnpm --filter @vexa/recording test`):
[`src/golden.test.ts`](src/golden.test.ts) reproduces the shared `recording.v1` golden vectors
byte-for-byte (the same vectors meeting-api's Python is tested against — a pass on both proves the two
implementations are in sync); [`src/assemble.smoke.test.ts`](src/assemble.smoke.test.ts) pins
`buildRecordingMaster` in isolation (WAV header-strip + payload-concat + size-correction, fmt-mismatch
throws; WebM byte-concat). The live acquire/deliver paths (parecord, ffmpeg, MediaRecorder, HTTP upload)
need an **integration env** (meeting-api + PulseAudio/Xvfb). Covered by `gate:node`, `gate:isolation`,
`gate:exports`, `gate:readme`.
