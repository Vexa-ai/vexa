# @vexa/recorder — the capture.v1 fixture recorder

A **tee on `capture.v1`**: `RawCaptureService implements CaptureV1Sink`, so the
host composes `tee(pipelineSink, recorderSink)` — every capture.v1 message is
forwarded to the pipeline unchanged **and** serialized to disk. *Recording is
configuration, not code change.* This is the **debug-fixture** recorder (frames +
hints, to replay the pipeline offline); the *deliverable media file* is a
different brick, [`../recording`](../recording/).

A captured fixture is **everything the page produced**, on the real meeting clock
— so one fixture replays the entire downstream chain deterministically, no meeting
needed. Run the WS recorder and drive the extension at it:

```bash
npm run capture     # WS recorder on :9099 → writes a capture.v1 fixture; Ctrl-C to finalize
```

## Files
- `src/raw-capture.ts` — the sink: per-speaker WAVs + `events.txt` + `meta.json`
  (the selection index: platform, num_speakers, speakers[], topology).
- `src/stream-capture.ts` — the streaming capture.v1 writer.
- `src/s3-upload.ts` — `uploadCaptureToS3`: partitioned prefix
  `telemetry/capture/v1/platform=<p>/date=<YYYY-MM-DD>/<meetingId>/` (no DB).
- `src/retention.ts` — rolling-window prune of the local corpus.
- `src/contracts/` — mirror of the canonical wire contract (`@vexa/capture-codec`).
- `scripts/` — `capture-recorder.mjs` (the WS recorder) · `dump.mjs`
  (`npm run dump` / `sweep`) · `select.sh` (query the corpus by
  platform/speakers/date) · `deepgram-benchmark.mjs` / `deepgram-diarize.mjs`
  (single-shot ground truth).

## Two sinks
Training corpus (full content, private S3, always-on under ToS) vs fixtures
(shareable, PII-gated). Promoting a corpus capture into a fixture is the gate.
Fixtures live in `$VEXA_FIXTURE_CACHE`, **never in the repo**.

## Gates
`npm run check:isolation` · `npm run build`.
