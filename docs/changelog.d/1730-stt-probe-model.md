- **The STT health probe asks for `TRANSCRIPTION_MODEL`, so model-validating backends stop refusing
  every bot (#1730).** meeting-api's boot/`/health` probe always sent `whisper-1`; a backend that
  validates model ids (DeepInfra, Groq, vLLM) answered 404, which read as a wrong endpoint and
  refused every spawn even though bots would have transcribed. The probe — and the Settings
  **Test** button for the deployment's backend — now send the same model the bot does. See
  [Bring your own STT](/how-to/custom-stt).
