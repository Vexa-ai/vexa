# TTS Service

## Why

Bot containers need text-to-speech to participate as voice agents in meetings. The TTS service provides local speech synthesis with zero external API calls — fully self-hosted, no API keys to manage, no network dependency on external providers.

## What

OpenAI-compatible `/v1/audio/speech` endpoint backed by **Piper TTS** local ONNX inference. There are no synth-time external network calls for prepared voices; remaining voices are auto-downloaded from HuggingFace on first request.

The endpoint shape stays OpenAI-compatible so clients can reuse their existing request body and consume interchangeable audio bytes.

Input: JSON body
```json
{"model": "tts-1", "input": "Hola, ¿cómo estás?", "voice": "auto", "response_format": "wav"}
```

- `voice`: explicit Piper name (`en_US-amy-medium`), OpenAI-style alias (`alloy`/`nova`/...), or `auto` (auto-detects language from `input` and picks a matching voice; supported major languages are prepared by default).
- `response_format`: `wav` (default) or `pcm` (raw Int16LE 24kHz mono).
- `model`: accepted for OpenAI API compatibility and ignored by Piper.

Output: audio bytes (WAV 24kHz mono by default, or raw PCM).

In live meetings this service is used only for text requests to `POST /bots/{platform}/{native_meeting_id}/speak`. Meeting API publishes a `speak` command, the bot calls this service, and the returned PCM is played through the bot's PulseAudio virtual microphone. Pre-rendered `audio_url` and `audio_base64` `/speak` requests bypass this service; the bot decodes those files directly and plays them through the same microphone path.

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

- **Piper TTS** (bundled) — local ONNX inference, no external calls at synth time for prepared voices.
- No database, no Redis, no other Vexa services.
- No API keys required for speech synthesis.

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
  -d '{"model": "tts-1", "input": "Hello world", "voice": "auto", "response_format": "wav"}' \
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
