- **Lite: bots no longer echo in Google Meet (#819).** Vexa Lite left the bot's microphone path
  (`tts_sink` / `virtual_mic`) unmuted by default, so meeting audio looped back and participants
  heard themselves. Lite now mutes the mic by default like the per-meeting bot does; on-demand
  speaking still unmutes for the utterance. See [Deployment](/deployment).
