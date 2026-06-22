# recording-players — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> Presentational React players for a meeting recording: AudioPlayer ({src? | fragments?, onTimeUpdate?}) and VideoPlayer ({src, onTimeUpdate?}). Props in, DOM out — no store, no fetch, no websocket; the media URL(s) are injected. Clean modular rebuild of the vendored dashboard recording players, typed by @vexa/dash-contracts (RecordingMaster.raw_url / duration_seconds). The L4 gate mounts the components in a REAL chromium (Playwright) over golden props and asserts the rendered <audio>/<video> + controls in the DOM.

