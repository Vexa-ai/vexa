# TTS Service

## Why

Bot containers need text-to-speech to participate as voice agents in meetings, but they should not each hold OpenAI API keys or manage HTTP connections to external providers. The TTS service centralizes OpenAI TTS access behind a single internal endpoint, keeping API key management in one place and allowing future provider swaps without touching bot code.

## What

A lightweight FastAPI proxy that exposes an OpenAI-compatible `/v1/audio/speech` endpoint. It receives a JSON request with text, voice, and format parameters, streams the request to OpenAI's TTS API, and streams the audio response back to the caller. No database, no state -- pure passthrough with validation.

Input: JSON body `{"model": "tts-1", "input": "text to speak", "voice": "nova", "response_format": "pcm"}`
Output: Streaming audio (PCM 24kHz mono by default, or mp3/opus/aac/wav/flac)

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/v1/audio/speech` | Synthesize text to speech (OpenAI-compatible) |

Supported voices: `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`
Supported formats: `pcm`, `mp3`, `opus`, `aac`, `wav`, `flac`

### Dependencies

- **OpenAI API** -- requires a valid API key with TTS access
- No database, no Redis, no other Vexa services

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
| `OPENAI_API_KEY` | OpenAI API key for TTS synthesis (required for operation) |
| `OPENAI_BASE_URL` | OpenAI API base URL (default: `https://api.openai.com`) |
| `TTS_API_TOKEN` | Optional access token -- if set, requests must include `X-API-Key` header |

### Test

```bash
# Health check
curl http://localhost:8002/health

# Synthesize speech (save as PCM)
curl -X POST http://localhost:8002/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model": "tts-1", "input": "Hello world", "voice": "nova", "response_format": "pcm"}' \
  --output speech.pcm
```

### Debug

- Logs to stdout: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
- If `OPENAI_API_KEY` is not set, the service starts but returns 503 on synthesis requests
- 502 errors mean OpenAI returned an error -- the first 200 chars of the upstream error are included in the response
- Invalid voice names silently fall back to `alloy`; invalid formats fall back to `pcm`
