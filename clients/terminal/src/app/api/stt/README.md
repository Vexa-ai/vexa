# api/stt (composer dictation STT proxy)

`POST /api/stt[?prompt=…]` with an `audio/wav` body (16 kHz mono PCM from
`ui-kit/micDictation`). Forwards to the same OpenAI-compatible transcription
service the meeting pipeline uses (`TRANSCRIPTION_SERVICE_URL`, contract per
`@vexa/transcribe-whisper`); the bearer token stays server-side. Returns
`{ text, words }` — word timestamps drive the client's LocalAgreement
confirm/trim loop for streaming dictation. `prompt` carries already-confirmed
text for Whisper context continuity. Unconfigured deployments answer 503 with
a human-readable error instead of failing silently.
