# @vexa/transcribe-whisper — the shared stt.v1 egress

_meetings/ · module · the single chokepoint to the hosted Whisper service._

One concern: take a PCM window → call the hosted transcription-service (OpenAI-
compatible `verbose_json`) → return word-level segments, with the **low-confidence /
hallucination STT filter applied at source**. Both lanes drive the shared buffer,
which calls this via an injected `transcribe(pcm, prompt)` fn — so whisper knows
nothing of topology, naming, or confirmation.

- `isLowConfidenceSegment` drops acoustically-junk segments (bad logprob, high
  no-speech, runaway compression) before they reach the confirm loop.
- WAV-encodes Float32 PCM, retries transient failures with backoff, 30s timeout.
- ALLOY: `autoDetectLanguagePerSegment` is disabled by default. When explicitly
  enabled without a forced language, the adapter splits only at qualifying
  natural pauses, submits chunks sequentially, and merges verbose text,
  segment/word offsets, duration, and `mul` language metadata. The caller still
  observes one limiter lifecycle; any child failure rejects the logical call.
- ALLOY: language switches without an acoustic pause remain backend-limited, and
  pause-rich windows trade additional inference latency for re-detection.

## Surface
`TranscriptionClient` · `isLowConfidenceSegment` · `setLogger` · types
`TranscriptionWord/Segment/Result`, `TranscriptionClientConfig`. Front door:
[`src/index.ts`](src/index.ts).

## Verify
```bash
pnpm --filter @vexa/transcribe-whisper build
pnpm --filter @vexa/transcribe-whisper test   # filters, limiter, pause split, merge/rollback
```
`gate:node` runs the **offline** adapter goldens here; the HTTP boundary is
exercised with complete verbose responses and then by pipeline replay (3.2) and
live L4. Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
