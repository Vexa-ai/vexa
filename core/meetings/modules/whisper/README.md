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

## Surface
`TranscriptionClient` · `isLowConfidenceSegment` · `setLogger` · types
`TranscriptionWord/Segment/Result`, `TranscriptionClientConfig`. Front door:
[`src/index.ts`](src/index.ts).

## Verify
```bash
pnpm --filter @vexa/transcribe-whisper build
pnpm --filter @vexa/transcribe-whisper test   # the stt.v1 low-confidence filter golden
```
`gate:node` runs the **offline** filter golden here; the HTTP client is exercised
end-to-end by pipeline replay (3.2) and live L4. Covered by `gate:node`,
`gate:isolation`, `gate:exports`, `gate:readme`.
