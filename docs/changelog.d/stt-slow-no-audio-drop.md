- **Meeting bots no longer lose speech when the transcription backend is slow.** On Google Meet
  and Zoom, a bot used to transcribe only part of a meeting when the STT took longer than a few
  seconds to answer: turns closed while a request was in flight, and audio that arrived across an
  input gap, were thrown away. Buffered audio is now always transcribed, however long the STT takes.
  The default per-request timeout is 90 s (`BOT_STT_TIMEOUT_SEC`). Turns shorter than a second are no
  longer sent (`BOT_SPEAKER_MIN_FINAL_AUDIO_SEC`), because they only came back as "Thank
  you.". Each bot logs a `[COVERAGE]` line with seconds captured, transcribed and dropped. See
  [Troubleshooting](/troubleshooting#transcript-covers-only-part-of-the-meeting).
