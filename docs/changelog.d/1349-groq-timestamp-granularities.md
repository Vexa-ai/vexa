- **Groq-backed transcription produces segments again: the STT client names the timestamp
  granularities as an array (#1349).** The bot's whisper client sent the form part
  `timestamp_granularities`; Groq's `/openai/v1/audio/transcriptions` validates the OpenAI audio
  schema and answers `400 unknown param` to that name, so every live segment of a Groq-pointed
  deployment failed and the meeting ended on its `stt_degraded` breaker. The client now sends
  `timestamp_granularities[]` — and asks for `segment` as well as `word`, because a word-only
  request answers `segments: null` and the client reads `data.segments`. Backends that ignore the
  field (the bundled `deploy/transcription` unit) are unaffected. See
  [Configuration](/configuration).
