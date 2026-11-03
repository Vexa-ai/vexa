- **Voice notes in Minutes (#1783).** The microphone in the chats rail records a note with no meeting
  to join. It is transcribed live by the same pipeline as a meeting and kept as a meeting (platform
  `voice_note`): page, transcript, notes, search, share and export work unchanged. Pause, resume and
  Stop are on the meeting page. The audio streams over `WS /voice-notes/{id}/stream` through the
  gateway; a note lasts at most `VOICE_NOTE_MAX_DURATION_SEC` (default 1800) and ends after
  `VOICE_NOTE_IDLE_TIMEOUT_SEC` (default 120) without audio or Stop; both are set in Compose's `.env`
  or Helm's `meetingApi.voiceNoteMaxDurationSeconds`/`voiceNoteIdleTimeoutSeconds`. A bot's Redis user may now also
  read its own note's audio stream. See [Voice notes](/minutes/voice-notes).
