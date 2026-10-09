Zoom per-track capture uses a shared epoch-anchored monotonic clock instead of
counting delivered samples. Delayed callbacks no longer accumulate timestamp drift,
and shared channel transcription preserves the retained audio timeline after full-text
confirmation. Capture gaps over one second emit rate-limited diagnostic observations.
