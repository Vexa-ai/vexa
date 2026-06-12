# acts.v1 — act commands into the meeting (to define at MVP3)

meeting-api / runtime-api → vexa-bot: speak (TTS), chat, screen content,
camera/avatar, microphone. Today these are bot HTTP endpoints
(`/bots/{platform}/{id}/speak|chat|screen` since v0.9); MVP3 names the command
schema. Asymmetry is structural: acts flow to the bot only — nobody can make
the user's extension speak.
