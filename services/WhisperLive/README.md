# WhisperLive

Real-time audio bridge for transcription. Fork of [collabora/WhisperLive](https://github.com/collabora/WhisperLive), running in **remote-only mode**.

> **Note:** The Vexa bot now has its own per-speaker transcription pipeline
> (direct HTTP POST to transcription-service, per-speaker buffers, VAD).
> WhisperLive is **optional** for bot-based meetings. It remains useful for
> standalone / external WebSocket clients that send a single mixed audio
> stream and need server-side buffering, LIFO scheduling, and segment
> delivery.

## Why

Transcription needs to happen in real-time — users see words appearing as people speak. The transcription service handles the model, but someone needs to manage the real-time flow: receive a continuous audio stream from a bot, decide when enough audio has accumulated to be worth transcribing, send it, get segments back, and deliver them to both the client and the persistence layer.

WhisperLive is that layer. It buffers audio intelligently (LIFO — always transcribe the latest audio first, skip stale chunks), manages per-meeting WebSocket connections, and publishes transcript segments to Redis for downstream consumption. Many concurrent meetings connect to one WhisperLive instance.

## What

| Component | Details |
|---|---|
| WebSocket server | Port 9090 — accepts bot audio connections |
| Health check | Port 9091 — `/health` endpoint for orchestration |
| Backend | Remote only — HTTP forwarding to `transcription-service` |
| Output | WebSocket segments to client + Redis stream (`transcription_segments`) |
| Capacity | 1000 concurrent connections (configurable) |

### Connection protocol

1. Client opens WebSocket to `ws://host:9090/ws`
2. Client sends JSON config: `{uid, platform, meeting_url, token, meeting_id, language, task, use_vad}`
3. All five identity fields (`uid`, `platform`, `meeting_url`, `token`, `meeting_id`) are **required** — connection is rejected without them
4. Client streams binary Float32 audio frames
5. Server responds with JSON segment updates: `{uid, segments: [{start, end, text, completed}]}`

### Architecture

- One `ServeClientRemote` instance per WebSocket connection
- LIFO buffer: always transcribes the latest audio, never queues old chunks
- Algorithm A: advances buffer only on VAD silence, completed segments, or same-output reconfirmation
- Durable recording spool: persists raw audio to disk for crash recovery

### LIFO under load

Each client has ONE in-flight transcription request at a time. When the transcription-service is fast (~0.2s), this is invisible. When it's saturated:

1. WhisperLive sends audio chunk to transcription-service
2. Service is busy — response takes 5-10s instead of 0.2s
3. New audio keeps arriving, WhisperLive buffers it
4. Response comes back → LIFO grabs the **latest** audio, discards old chunks
5. The user sees the most recent words, not stale audio from 10 seconds ago

This is by design. LIFO prioritizes freshness over completeness — better to transcribe what's being said NOW than fall behind processing old audio. Under heavy load, some audio is intentionally skipped so the transcript stays current.

### Capacity and scaling

~100 concurrent streams per transcription-service GPU for reliable transcription with `large-v3-turbo` (int8). Beyond that, LIFO skips more audio to stay current.

Scaling options:
- **Add transcription-service workers** — more GPUs = more inference capacity = less skipping
- **Pair WhisperLive with dedicated transcription capacity** — instead of one instance with 1000 connections, run multiple instances each with their own workers
- **Embed in the bot** — each bot runs its own WhisperLive, eliminating the shared bottleneck

## How

### Running (production)

WhisperLive runs as part of the main docker-compose stack:

```bash
docker compose up whisperlive
```

It requires `transcription-service` to be running and reachable at `REMOTE_TRANSCRIBER_URL`.

### Running (test)

```bash
# 1. Start transcription-service
cd services/transcription-service && docker compose up -d

# 2. Start WhisperLive test compose
cd services/WhisperLive
docker compose -f tests/docker-compose.test.yml up -d

# 3. Run tests
pytest tests/ -v                        # unit tests
bash tests/test_hot.sh --chain          # integration chain test
bash tests/test_stress.sh               # stress / concurrency test
```

### Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEVICE_TYPE` | `remote` | Backend type (always `remote` in our fork) |
| `REMOTE_TRANSCRIBER_URL` | — | HTTP endpoint for transcription-service |
| `REMOTE_TRANSCRIBER_API_KEY` | — | Auth key for transcription-service |
| `REDIS_STREAM_URL` | — | Redis connection for segment publishing |
| `REDIS_STREAM_KEY` | `transcription_segments` | Redis stream key |
| `MIN_AUDIO_S` | `1` | Minimum buffered audio (seconds) before transcription |
| `MIN_TIME_BETWEEN_REQUESTS_S` | `0.5` | Rate limit between remote API calls |
| `SAME_OUTPUT_THRESHOLD` | `3` | Repeated outputs needed to confirm a segment |
| `WL_RECORDING_DIR` | `/tmp/wl-recordings` | Durable audio spool directory |
| `WL_LOG_LEVEL` | `INFO` | Logging level |

### Debugging

```bash
# Container logs
docker compose logs -f whisperlive

# Health check
curl http://localhost:9091/health | python3 -m json.tool

# WebSocket test (quick connection verify)
python3 -c "
import asyncio, websockets, json, uuid
async def t():
    async with websockets.connect('ws://localhost:9090/ws') as ws:
        await ws.send(json.dumps({
            'uid': str(uuid.uuid4()), 'platform': 'test',
            'meeting_url': 'https://test', 'token': 'tok',
            'meeting_id': 'debug-' + str(uuid.uuid4())[:8],
        }))
        print(await asyncio.wait_for(ws.recv(), timeout=3))
asyncio.run(t())
"
```

### File layout

```
WhisperLive/
  run_server.py              # Entrypoint (argparse + server start)
  Dockerfile                 # Build (python:3.10-slim, remote-only)
  entrypoint.sh              # Container entrypoint
  healthcheck.sh             # Docker HEALTHCHECK script
  whisper_live/
    server.py                # TranscriptionServer, ServeClientRemote, Redis publisher
    remote_transcriber.py    # HTTP client for transcription-service
    settings.py              # All tunable parameters (env-driven)
    types.py                 # Dataclasses: Segment, VadOptions, etc.
  tests/
    docker-compose.test.yml  # Isolated test stack (ports 19090/19091)
    test_types.py            # Unit tests for types module
    test_hot.sh              # Integration / chain test
    test_stress.sh           # Stress / concurrency test
    AGENT_TEST.md            # Test objectives and plan
```

## Shared hallucination filter

The `hallucinations/` directory contains per-language phrase lists and a
reusable filter used by both WhisperLive and the bot:

```
hallucinations/
  en.txt, ru.txt, es.txt, pt.txt   # known junk phrases per language
  filter.py                         # Python filter (WhisperLive)
  filter.ts                         # TypeScript filter (bot)
  collect_hallucinations.py         # tool to gather new phrases from logs
  README.md                         # format and contribution guide
```

## License

MIT License — Copyright (c) 2023 Vineet Suryan, Collabora Ltd. See [LICENSE](LICENSE).
