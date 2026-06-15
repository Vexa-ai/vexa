# stt.v1 — speech to timestamped text

The proven contract: OpenAI-compatible audio API, live since v0.10. The seam
that ended the WhisperLive era — standard, swappable, testable with a curl.

- **Producer:** any stt-client (today: `vexa-bot` pipeline bricks)
- **Consumer:** `services/transcription-service` (or any OpenAI-compatible endpoint)
- **Standard:** OpenAI Audio API (`/v1/audio/transcriptions` shape). **Never fork it.**
- **Golden:** `examples/` holds a real recorded request/response pair from the
  live service. CI replay uses recorded responses (MANIFEST §4 trust contract
  rule 1) so pipeline oracles are deterministic.

Request: audio (wav/opus) + model + response_format=verbose_json.
Response: segments with start/end timestamps + text. See examples.
