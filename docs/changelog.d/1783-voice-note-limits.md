- **Voice-note audio limits (#1783).** The voice-note socket takes audio at up to 1.5× realtime after a
  5-second burst, stores at most one keepalive per 5 seconds, and closes with `rate_limited` (4429)
  past a text-frame budget; opening it spends from the gateway's per-user request budget. The note's
  Redis audio stream now expires minutes after its last write (was an hour past the cap), 5 minutes
  after Stop, and is deleted when the note ends or the meeting is deleted. The terminal server takes
  `/ws` and voice-note upgrades only from its own origin (or `NEXTAUTH_URL` / `TERMINAL_URL`), and a
  voice-note upgrade only with a session cookie. See [Voice notes](/minutes/voice-notes#retention).
