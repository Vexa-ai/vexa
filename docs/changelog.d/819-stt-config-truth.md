### Fixed

- **A wrong STT URL or token is now caught where you set it, not by an empty transcript.** The
  config probe treated *any* answer except 401/403 as proof of a working backend, so a URL whose
  transcriptions path 404s — the most common misconfiguration — passed every check. It is now
  reported as `misconfigured` with the reason, on the boot log and the `stt` row of `GET /health`.
  ([#511](https://github.com/Vexa-ai/vexa/issues/511))
- **The setup wizard's green now means "a bot will transcribe".** On any non-Vexa
  OpenAI-compatible endpoint the test previously reported "reachable; token was not verified" as a
  PASS, so a rejected key tested green and failed mid-meeting. It now verifies with the same
  request a bot's first audio chunk makes, and grades the endpoint's own answer.
  ([#511](https://github.com/Vexa-ai/vexa/issues/511))
- **`POST /bots` refuses a set-but-broken backend** with a typed 503 carrying the probe's reason,
  before the meeting row is written — instead of spawning a bot that joins and captures nothing.
  The refusal names how to re-test immediately. ([#511](https://github.com/Vexa-ai/vexa/issues/511))
- **`TRANSCRIPTION_SERVICE_URL` accepts both documented shapes everywhere.** A full
  `…/v1/audio/transcriptions` URL worked in meetings but double-pathed into a 404 in the boot probe
  and the terminal's dictation route. All four consumers now share one rule: append the path only
  when it is absent. ([#511](https://github.com/Vexa-ai/vexa/issues/511))
