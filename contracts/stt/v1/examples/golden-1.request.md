# golden-1 — request

POST $TRANSCRIPTION_SERVICE_URL   (OpenAI-compatible /v1/audio/transcriptions)
Authorization: Bearer $TRANSCRIPTION_SERVICE_TOKEN
Content-Type: multipart/form-data

| field | value |
|---|---|
| `file` | `golden-1.wav` (16 kHz mono LEI16 WAV, 5.9 s synthesized speech) |
| `model` | `whisper-1` |
| `response_format` | `verbose_json` |

Expected response: `golden-1.response.json` (exact match — replay is
deterministic because CI replays this recorded response, MANIFEST §4 rule 1).
