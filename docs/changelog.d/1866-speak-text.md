- **A bot can speak in the meeting (#1866).** `POST /bots/{platform}/{native_meeting_id}/speak` with
  `{"text": "…"}` makes a bot started with `"voice_agent_enabled": true` say the line aloud on Google
  Meet, Microsoft Teams, Zoom or Jitsi, and `DELETE …/speak` stops it. The meeting's owner or a workspace
  owner/contributor may call it; 500 characters a request, 6 requests a minute per meeting. Set
  `TTS_SERVICE_URL` (an OpenAI-compatible endpoint), `TTS_API_TOKEN` and `TTS_VOICE` on the deployment.
  See [Meetings API](/api/meetings) and [Configuration](/configuration#bot-speech-tts).
