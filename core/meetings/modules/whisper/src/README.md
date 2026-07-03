# whisper/src

Front door [`index.ts`](index.ts). [`transcription-client.ts`](transcription-client.ts)
is the HTTP client to transcription-service; [`confidence.ts`](confidence.ts) is the
pure low-confidence filter (pinned by `confidence.test.ts`); [`log.ts`](log.ts) is the
host-injectable logger.
