- **Set the transcription language before and during a meeting.** A deployment default (Helm
  `meetingApi.transcriptionLanguage` / `transcriptionAllowedLanguages`), a per-person default in
  Settings → Transcription language, and a per-meeting choice when sending a bot. The first that is set
  wins. "A few languages" (`allowed_languages`, e.g. German and English) restricts detection to the list
  and falls back to one language when something else is detected. On the live meeting page, the owner
  or a workspace editor can switch the language; the chat agent can too (`update_bot_config`).
  `PUT /bots/{platform}/{native_meeting_id}/config` now works. See
  [Transcription language](/how-to/transcription-language).
