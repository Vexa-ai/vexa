- **Meeting bots can now receive voice-agent speech commands (#514).** Authenticated `POST` and
  `DELETE /bots/{platform}/{id}/speak` requests reach the active bot through `acts.v1`; text uses
  the existing TTS path and supplied WAV or PCM audio plays through the virtual microphone. This
  change is service- and module-tested; the live Google Meet witness remains pending.
