# TTS Service

## Why

Bot containers need text-to-speech to participate as voice agents in meetings. The TTS service provides local speech synthesis with zero external API calls — fully self-hosted, no API keys to manage, no network dependency on external providers.

## What

A local text-to-speech service using **Piper TTS** (ONNX models). Exposes an OpenAI-compatible `/v1/audio/speech` endpoint so existing code that targets OpenAI's TTS API works without changes. Voice models are auto-downloaded from HuggingFace on first use.

Input: JSON body `{"model": "tts-1", "input": "text to speak", "voice": "nova", "response_format": "wav"}`
Output: Audio (WAV 24kHz mono by default, or raw PCM)

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

- **Piper TTS** (bundled) — local ONNX inference, no external calls
- No database, no Redis, no other Vexa services
- No API keys required for operation

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
| `PIPER_DEFAULT_VOICES` | Comma-separated voices to pre-load on startup (default: `en_US-amy-medium,en_US-danny-low`) |
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
- Voice models are downloaded on first use — first request for a new voice will be slower
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

Confidence: 0 (untested)
