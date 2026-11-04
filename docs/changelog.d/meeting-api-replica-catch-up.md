- **A meeting's status trail no longer loses steps across meeting-api replicas.** A replica that had seen
  only `joining` could receive the bot's lobby-timeout `failed` after another replica had recorded
  `awaiting_admission`; it wrote its own shorter trail over the meeting and reported
  `failure_stage: joining`. Each lifecycle event now catches the replica up from the stored trail when
  that trail is longer, so `failure_stage`, the lobby step and the admission time stay as the bot
  reported them.
