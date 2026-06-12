# capture.v1 — capture-kit → pipeline

Output of capture-kit, input to the pipeline bricks. The recorder (P5) is a
**tee** on this seam: forward to pipeline unchanged + serialize a copy. One
format, three jobs — wire format, recorder output, pipeline replay input.

- **schema.ts** — the typed contract: `AudioChunk`, `MeetingEvent`, `CaptureMeta`,
  the `CaptureV1Sink` port, and `tee()` (compose pipeline + recorder).
- **example.capture.json** — golden: a short envelope (no audio bytes) showing
  the event sequence + chunk descriptors.

Status: in-process code contract today (vexa-bot routes the per-speaker audio
callback through `CaptureV1Sink`). Becomes the wire boundary when capture-kit
and pipeline extract as bricks (MVP2). The `ingest-server` WS frames are the
wire form of this same contract (the extension already speaks it).

Fixture/corpus governance: §4 tiers. `prod-full` (consented) carries
`text`/audio; `prod-envelope` (always-on, no PII) carries chunk descriptors +
event occurrences only.
