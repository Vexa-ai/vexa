- **Seat your own voice agent in a meeting by URL (#333).** `POST /bots` accepts
  `voice_agent_settings.url`: the bot opens that page, gives it the meeting's audio as its microphone,
  plays its voice into the meeting, and shows the page as the bot's camera (Google Meet, Zoom, Jitsi;
  Teams without camera). The URL is per bot and checked like a webhook URL — public addresses only, `422`
  otherwise; self-hosted deployments can allow their own agent host with `VEXA_AGENT_URL_ALLOWLIST`.
  `voice_agent_enabled` now reaches the bot instead of being dropped at spawn. See
  [Interactive bots](/interactive-bots#seat-an-external-voice-agent).
