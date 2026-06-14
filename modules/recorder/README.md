# recorder — the recorder brick (MANIFEST P5)

A **tee on `capture.v1`**: `RawCaptureService implements CaptureV1Sink`, so the
host composes `tee(pipelineSink, recorderSink)` — every capture.v1 message is
forwarded to the pipeline unchanged AND serialized to a sink. *Recording is
configuration, not code change.*

- `src/raw-capture.ts` — the sink: per-speaker WAVs + `events.txt` + `meta.json`
  (the selection index: platform, num_speakers, speakers[], topology).
- `src/s3-upload.ts` — `uploadCaptureToS3`: partitioned prefix
  `telemetry/capture/v1/platform=<p>/date=<YYYY-MM-DD>/<meetingId>/` (no DB).
- `src/contracts/` — mirror of canonical `contracts/capture/v1`.
- `scripts/select.sh` — query the corpus by platform/speakers/date (no DB).
- `scripts/deepgram-benchmark.mjs` — single-shot ground truth (WER, analytics).

## Two sinks (MANIFEST §4 / P5)
Training corpus (full content, private S3, always-on under ToS) vs fixtures
(shareable, PII-gated). Promotion of a corpus capture into a fixture is the gate.

Gates: `npm run check:isolation` · `npm run build`.
