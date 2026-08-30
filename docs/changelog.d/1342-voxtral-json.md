- **Custom STT: JSON-only Voxtral models now transcribe meetings.** Vexa negotiates down from
  `verbose_json` to `json` once when a backend rejects verbose output, while Whisper backends keep
  their richer segment metadata. See [Use a custom STT endpoint](/how-to/custom-stt).
