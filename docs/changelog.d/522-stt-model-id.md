- **STT model id is now a deployment choice: `TRANSCRIPTION_MODEL` (#522).** The bot's whisper
  client used to hardcode `model=whisper-1` on every live transcription request, so OpenAI-compatible
  backends that validate model ids (Groq, vLLM, LiteLLM, gateways) rejected every request — the bot
  joined and captured but produced no transcript. Set `TRANSCRIPTION_MODEL` (e.g.
  `whisper-large-v3-turbo` for Groq) in the stack `.env` and every request — bot pipeline and the
  terminal's composer-mic dictation — carries that id end-to-end; unset, the wire stays `whisper-1`
  byte-for-byte. The bundled `deploy/transcription` unit still ignores the field (its model is its
  own `MODEL_SIZE`). Credit: @waqaskhan137's #489 prototyped the client-side half. See
  [Configuration](/configuration#transcription-stt).
