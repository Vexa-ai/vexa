- **Self-hosted bots transcribe again against Vexa's hosted transcription service (#1344).** Every
  transcription request now carries an `Idempotency-Key` header, which the hosted service requires;
  without it every audio window failed with HTTP 400 and meetings ended with an empty transcript.
  The key is fresh per audio window and reused when the bot retries that window. The terminal's
  dictation, the boot preflight probe and the Settings "Test" button send it too. See
  [Use a custom STT endpoint](/how-to/custom-stt).
