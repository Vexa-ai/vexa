# TTS Service

## Why

Bot containers need text-to-speech to participate as voice agents in meetings. The TTS service provides local speech synthesis with zero external API calls — fully self-hosted, no API keys to manage, no network dependency on external providers.

## What

OpenAI-compatible `/v1/audio/speech` endpoint with **two backends** the caller picks per-request:

| `provider` | Backend | Network calls | Auth | Quality |
|---|---|---|---|---|
| `piper` *(default)* | [Piper TTS](https://github.com/rhasspy/piper) — local ONNX inference | None at synth time for prepared voices; remaining voices auto-download from HuggingFace on first request | None required | Robotic but clear; ~30 languages |
| `openai` | OpenAI `https://api.openai.com/v1/audio/speech` | Yes — every request | `OPENAI_API_KEY` env var | Higher; counts against your OpenAI usage |

The endpoint shape stays identical — callers swap `provider` and the response is interchangeable audio bytes.

Input: JSON body
```json
{"model": "tts-1", "input": "Hola, ¿cómo estás?", "voice": "auto", "response_format": "wav", "provider": "piper"}
```

- `provider`: `piper` (default) or `openai`. Omitted = `piper`.
- `voice`: explicit Piper name (`en_US-amy-medium`), OpenAI alias (`alloy`/`nova`/...), or `auto` (Piper provider only — auto-detects language from `input` and picks a matching voice; supported major languages are prepared by default).
- `response_format`: `wav` (default) or `pcm` (raw Int16LE 24kHz mono).
- `model`: ignored by Piper, passed through to OpenAI.

Output: audio bytes (WAV 24kHz mono by default, or raw PCM).

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (includes loaded voices and aliases) |
| GET | `/voices` | List downloaded voices and known aliases |
| POST | `/v1/audio/speech` | Synthesize text to speech (OpenAI-compatible) |

### Voice mapping

OpenAI voice names are mapped to Piper voices for backward compatibility:

| OpenAI name | Piper voice |
|-------------|-------------|
| `alloy` | `en_US-amy-medium` |
| `echo` | `en_US-danny-low` |
| `fable` | `en_US-joe-medium` |
| `onyx` | `en_US-ryan-medium` |
| `nova` | `en_US-kristin-medium` |
| `shimmer` | `en_US-lessac-medium` |

You can also use Piper voice names directly (e.g. `en_US-amy-medium`).

Supported formats: `wav` (default), `pcm` (raw Int16LE 24kHz mono)

### Dependencies

- **Piper TTS** (bundled) — local ONNX inference, no external calls.
- **OpenAI TTS** (optional) — only invoked when callers pass `provider=openai`. Requires `OPENAI_API_KEY`.
- No database, no Redis, no other Vexa services.
- No API keys required for the default Piper provider.

## How

### Run

```bash
# Via docker-compose (from repo root)
docker compose up tts-service

# Standalone
cd services/tts-service
uvicorn main:app --host 0.0.0.0 --port 8002
```

### Configure

| Variable | Description |
|----------|-------------|
| `TTS_API_TOKEN` | Optional access token — if set, requests must include `X-API-Key` header |
| `TTS_OUTPUT_SAMPLE_RATE` | Output sample rate (default: `24000`) |
| `PIPER_VOICES_DIR` | Voice model storage directory (default: `/app/voices`) |
| `PIPER_DEFAULT_VOICES` | Comma-separated voices to prepare on startup, or `major` (default) for the release-supported major language set |
| `PIPER_LOAD_VOICES` | Comma-separated prepared voices to also keep loaded in memory (default: English + Portuguese + Spanish) |
| `PIPER_PRELOAD_STRICT` | If true, startup fails when a configured voice cannot be prepared (default: `true`) |
| `LOG_LEVEL` | Logging level (default: `INFO`) |

### Test

```bash
# Health check
curl http://localhost:8002/health

# List voices
curl http://localhost:8002/voices

# Synthesize speech (save as WAV)
curl -X POST http://localhost:8002/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model": "tts-1", "input": "Hello world", "voice": "nova", "response_format": "wav"}' \
  --output speech.wav
```

### Debug

- Logs to stdout: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
- Major supported voice models are prepared on startup; first request for a non-prepared voice may still be slower
- Invalid voice names that can't be downloaded return 404
- The `model` field is accepted but ignored (kept for OpenAI API compatibility)

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | `GET /health` returns 200 with loaded voices list | 20 | ceiling | untested | — | — | — |
| 2 | `POST /v1/audio/speech` returns valid WAV audio for text input | 30 | ceiling | untested | — | — | — |
| 3 | Default Piper voices downloaded and loaded on startup | 20 | ceiling | untested | — | — | — |
| 4 | `GET /voices` returns available voice names and aliases | 15 | — | untested | — | — | — |
| 5 | OpenAI voice name aliases resolve to correct Piper voices | 15 | — | untested | — | — | — |

Confidence: 30 (indirect evidence only: speaking-bot feature uses POST /v1/audio/speech and produces audible TTS in meetings. No direct tests3 coverage — no TTS health check, /voices endpoint, or alias mapping tested.)
