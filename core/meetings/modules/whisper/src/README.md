# whisper/src

Front door [`index.ts`](index.ts). [`transcription-client.ts`](transcription-client.ts)
is the HTTP client to transcription-service; [`confidence.ts`](confidence.ts) is the
pure low-confidence filter (pinned by `confidence.test.ts`); [`log.ts`](log.ts) is the
host-injectable logger. ALLOY: [`pause-segmenter.ts`](pause-segmenter.ts) owns the
opt-in natural-pause ranges used by `auto-language.test.ts` to prove sequential
no-language requests, original-window offsets, atomic failure, and rollback.
