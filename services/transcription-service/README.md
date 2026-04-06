# Transcription Service

## Why

GPU inference is expensive, stateful, and hardware-specific. You don't want every service that needs transcription to manage its own model, CUDA runtime, and GPU memory. The transcription service isolates all of that behind a standard OpenAI-compatible API — separation of concerns.

Any client that speaks the OpenAI Whisper API can use it. The bot pipeline uses it for real-time meeting transcription. But it's a standalone, general-purpose service — not tied to Vexa. Send audio, get text back.

Under the hood: faster-whisper behind an Nginx load balancer. Add workers to scale. GPU or CPU. One API endpoint, one docker-compose command.

### Documentation
- [Concepts](../../docs/concepts.mdx)

## What

- **OpenAI Whisper API compatible** (`/v1/audio/transcriptions`) -- works with any client that speaks the OpenAI audio API.
- **Load-balanced** -- Nginx distributes requests across workers using least-connections.
- **Backpressure-aware** -- configurable fail-fast mode returns 503 when busy, letting callers buffer and retry.
- **GPU and CPU** -- same codebase, different docker-compose files.

Ships with one worker. Add more by uncommenting worker definitions in `docker-compose.yml` and `nginx.conf`. Each worker needs one GPU.

## How

### Run

```bash
# Copy and edit environment
cp .env.example .env
# Edit .env -- see docs/models.md for model/compute guidance

# Start (GPU)
docker compose up -d

# Start (CPU)
docker compose -f docker-compose.cpu.yml up -d

# Watch logs until "Model loaded successfully"
docker compose logs -f
```

The service listens on the port mapped in `docker-compose.yml` (default 8083:80).

### Test

```bash
# Health check
curl http://localhost:8083/health

# Transcribe a file
curl -X POST http://localhost:8083/v1/audio/transcriptions \
  -H "X-API-Key: $API_TOKEN" \
  -F "file=@tests/test_audio.wav" \
  -F "model=whisper-1" \
  -F "response_format=verbose_json"

# Smoke test (service must be running)
bash tests/test_hot.sh --verify

# Stress test
bash tests/test_stress.sh

# Unit tests
pytest tests/ -v
```

### Configure

All configuration is via environment variables. Copy `.env.example` and adjust.

**Key variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_SIZE` | `large-v3-turbo` | Whisper model (see [docs/models.md](docs/models.md)) |
| `DEVICE` | `cuda` | `cuda` or `cpu` |
| `COMPUTE_TYPE` | `int8` | `int8`, `float16`, or `float32` |
| `CPU_THREADS` | `0` (auto) | CPU threads when `DEVICE=cpu` |
| `API_TOKEN` | (none) | Bearer token for authentication |
| `MAX_CONCURRENT_TRANSCRIPTIONS` | `2` | Concurrent model calls per worker |
| `FAIL_FAST_WHEN_BUSY` | `true` | Return 503 immediately when busy |
| `BUSY_RETRY_AFTER_S` | `1` | Retry-After header value (seconds) |
| `REPETITION_PENALTY` | `1.1` | Penalize repeated tokens (>1.0 = penalize) |
| `NO_REPEAT_NGRAM_SIZE` | `3` | Hard-block any N-word phrase from repeating |
| `VAD_MAX_SPEECH_DURATION_S` | `15.0` | Max segment length before forced split. Lower = shorter segments = faster pipeline confirmation |
| `VAD_MIN_SILENCE_DURATION_MS` | `160` | Min silence to trigger segment split |

These can be overridden per-request via form fields `max_speech_duration_s` and `min_silence_duration_ms`.

Full list with quality/VAD tuning parameters: `.env.example`.

### Response format

The `/v1/audio/transcriptions` endpoint returns JSON with:

```json
{
  "text": "transcribed text",
  "language": "en",
  "language_probability": 0.98,
  "duration": 5.2,
  "segments": [{"start": 0.0, "end": 5.2, "text": "transcribed text"}]
}
```

- `language_probability` -- confidence (0.0-1.0) of the detected language. The bot uses this to decide whether to lock language detection or keep auto-detecting.
- `segments` -- segment-level timing for the transcription.

### Word-level timestamps

Request `timestamp_granularities=word` to get per-word timing in the response:

```bash
curl -X POST http://localhost:8083/v1/audio/transcriptions \
  -H "X-API-Key: $API_TOKEN" \
  -F "file=@audio.wav" \
  -F "model=whisper-1" \
  -F "response_format=verbose_json" \
  -F "timestamp_granularities=word"
```

Response segments include a `words` array:

```json
{
  "segments": [{
    "start": 0.0, "end": 3.76,
    "text": " Hello everyone, this is a test.",
    "words": [
      {"word": " Hello", "start": 0.0, "end": 0.44, "probability": 0.91},
      {"word": " everyone,", "start": 0.44, "end": 0.98, "probability": 0.78},
      {"word": " this", "start": 1.52, "end": 2.06, "probability": 0.98}
    ]
  }]
}
```

Used by the bot pipeline for speaker attribution on Teams' single-channel mixed audio: caption says "Alice spoke 10.0s-15.2s" → match word timestamps → attribute those words to Alice.

Default (`timestamp_granularities=segment`) returns no `words` array — no performance impact.

### Scale

To add or remove workers, edit `docker-compose.yml` (add/uncomment worker service definitions) and `nginx.conf` (add/uncomment upstream entries), then restart:

```bash
docker compose up -d
```

### Troubleshoot

```bash
# Check all logs
docker compose logs

# Check a specific worker
docker compose logs transcription-worker-1

# Check load balancer status
curl http://localhost:8083/lb-status

# Verify GPU is visible
docker compose exec transcription-worker-1 nvidia-smi

# Test nginx config
docker compose exec transcription-api nginx -t
```

**Common issues:**
- **GPU not available** -- use `docker-compose.cpu.yml` instead.
- **Out of memory** -- switch to a smaller model (see [docs/models.md](docs/models.md)).
- **Port conflict** -- change the host port in `docker-compose.yml`.

### Known limitations

| Area | Status | Detail |
|------|--------|--------|
| **Certainty** | HIGH | API and config well documented |
| **Single GPU capacity** | Known | Single GPU on BBB handles ~2 concurrent meetings. Beyond that, queuing increases and LIFO skipping kicks in. |
| **Whisper hallucination on silence (bug #24)** | Known | When audio contains silence or very low-level noise, Whisper can hallucinate content (e.g., phantom "fema.gov" segment). Mitigation: bot-side hallucination filter in `core/src/services/hallucinations/`. New patterns should be added to the filter list. Also: `REPETITION_PENALTY=1.1` and `NO_REPEAT_NGRAM_SIZE=3` help reduce repetitive hallucinations. |
| **Naming mismatch: TRANSCRIBER_URL vs TRANSCRIPTION_SERVICE_URL** | Known | Bot containers use `TRANSCRIPTION_SERVICE_URL`, meeting-api supervisord config used to reference `TRANSCRIBER_URL`. Fixed in bug #23 (2026-04-05). When adding new deployment modes, ensure the correct env var name is used. |

## Integration with Vexa

Set these in the Vexa gateway environment:

```bash
REMOTE_TRANSCRIBER_URL=http://localhost:8083/v1/audio/transcriptions
TRANSCRIPTION_SERVICE_API_TOKEN=<same value as API_TOKEN above>
```

## Public Docs

- [Concepts](https://docs.vexa.ai/concepts)
- [Recording & Storage](https://docs.vexa.ai/recording-storage)

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | `GET /health` returns 200 (Nginx + at least one worker up) | 15 | ceiling | untested | — | — | — |
| 2 | `POST /v1/audio/transcriptions` returns transcript JSON for valid audio file | 30 | ceiling | untested | — | — | — |
| 3 | Model loaded successfully on worker startup (logs confirm) | 20 | ceiling | untested | — | — | — |
| 4 | Backpressure: returns 503 with Retry-After when all workers busy (`FAIL_FAST_WHEN_BUSY=true`) | 15 | — | untested | — | — | — |
| 5 | Nginx load balancer distributes across configured workers (`GET /lb-status`) | 10 | — | untested | — | — | — |
| 6 | `API_TOKEN` set and unauthenticated requests rejected | 10 | — | untested | — | — | — |

Confidence: 0 (untested)

## License

Apache-2.0
