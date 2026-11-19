- **Room noise no longer reaches the transcription service.** A meeting bot sends a speaker's window
  only when it holds at least 0.25 s of audio clearly above that speaker's own noise floor, so a
  fan, a hum or a far keyboard on a silent call no longer comes back as invented text in a random
  language. The bar never rises above 0.02 RMS, so steady, loud speech is always sent. A silent
  window cut at the size cap no longer drops the speech that follows it. In Auto, a speaker's
  language is pinned once two answers agree on it.
- **Dictation drops phrases Whisper invents on silence.** The composer removes lines such as
  "Продолжение следует…", subtitle credits, "Thank you." and "Thanks for watching" (the same lists
  the meeting bot uses), collapses a word looped three or more times, and sends only windows that
  carry speech above the room's noise floor.
- **Optional confidence filter for meeting transcripts:** `BOT_STT_CONFIDENCE_FILTER=strict` also
  drops segments that Whisper decoded with an average log-probability under −1.0. It is off by
  default. Each drop is logged as `[STT-CONFIDENCE]` with its scores.
