# prod-telemetry — production capture for replay (brick, incubating)

The recorder primitive (MANIFEST P5) shipped to production. Captures real
`capture.v1` traffic to S3 so any meeting can be replayed as if live, and runs
a Deepgram single-shot pass for ground-truth benchmarking + analytics.

## Two sinks, two governance rules

- **Training/analytics corpus** — full content (audio + transcripts), private
  access-controlled S3, governed by Vexa's ToS/privacy policy. **Always-on**
  in prod (`TELEMETRY_FULL=on`). This is the data you fetch for training the
  pipeline. `RAW_CAPTURE=true` is the per-meeting dev-debug alias of the same path.
- **Fixtures** — captures *promoted* into shareable test artifacts (committed,
  attached to issues). These travel, so they stay under MANIFEST §4 PII tiers:
  `prod-envelope` (no-PII shape) freely; full content only `internal`/consented/
  redacted. Promotion to a fixture is the gate, not collection.

## What it records (per meeting, S3 only — no DB)

`RAW_CAPTURE=true` on the bot (`services/vexa-bot/core/src/services/raw-capture.ts`)
already dumps per-speaker WAVs + `events.txt`; this brick adds:
- `meta.json` — the selection index: platform, num_speakers, language, speakers[],
  duration, started/ended, connection_events. Query without a database.
- `[LIFECYCLE]` events — ws connect/disconnect/reconnect (envelope spec: prime
  suspects in silences).
- partitioned S3 upload on finalize (`uploadCaptureToS3` in `s3-sync.ts`):
  `s3://$TELEMETRY_S3_BUCKET/telemetry/capture/v1/platform=<p>/date=<YYYY-MM-DD>/<meetingId>/`

## Selection (no database — the S3 prefix + meta.json IS the index)

```bash
# all Teams captures from a date
aws s3 ls s3://$BUCKET/telemetry/capture/v1/platform=teams/date=2026-06-12/
# filter by num_speakers / language: list meta.json, jq the field
scripts/select.sh --platform google_meet --min-speakers 3
```

## Ground truth (Deepgram single-shot)

`scripts/deepgram-benchmark.mjs <capture-dir|s3-uri>` — sends each speaker WAV
to Deepgram pre-recorded (nova-3) once, writes `ground_truth.json` alongside the
capture. Used to: (a) benchmark our realtime pipeline (WER vs Deepgram), (b)
feed analytical AI. Off the bot's hot path — an offline analytics pass over the
S3 corpus.

## Provenance & PII (MANIFEST §4)

`RAW_CAPTURE_PROVENANCE` tags each capture: `prod-full` (consented scopes) /
`internal` (our meetings). Unconsented customer content stays out until the
redaction pipeline exists. `prod-envelope` (no-PII shape tier) is the
always-on default; full content only on consented scopes.
