# transcription — the STT worker (faster-whisper, OpenAI-compatible)

The speech-to-text brick. A standalone FastAPI worker wrapping **faster-whisper / CTranslate2**
behind the OpenAI audio API (`POST /v1/audio/transcriptions`). The bot's whisper client
([`core/meetings/modules/whisper`](../../modules/whisper)) streams audio windows here and gets
segments back; meeting-api's `collector` ingests those segments into `transcription_segments`
and fans them out. Any OpenAI-audio-compatible client works — it is not tied to Vexa.

## Why it is its own brick, deployed separately

GPU inference is expensive, stateful, and hardware-specific. Bundling it into the main
`make all` compose stack would force every self-host onto an NVIDIA GPU. So the GPU workload is
**carved out**: this brick ships its own deploy unit ([`deploy/transcription`](../../../../deploy/transcription))
that you stand up separately (GPU or CPU), and the main stack reaches it over the network via
`TRANSCRIPTION_SERVICE_URL` (base URL; the client appends `/v1/audio/transcriptions`).

It imports only third-party libraries and its own module — no cross-brick edges (gate:isolation-py).

## Surface

| Route | Purpose |
|---|---|
| `POST /v1/audio/transcriptions` | OpenAI Whisper-compatible transcription (multipart audio → verbose_json segments) |
| `GET /health` | `200` when the selected backend is ready, `503` otherwise — includes `backend`; Transcribe failures include `reason` |
| `GET /` | service info (worker id · model · device) |

## Run

```bash
python -m transcription          # uvicorn worker on :8000 (loads MODEL_SIZE on first use)
uv run pytest -q                 # the autonomous contract suite (no GPU, no model download)
```

Container builds: `Dockerfile` (GPU, `nvidia/cuda` base) and `Dockerfile.cpu` (CPU-only).
Config (env): `MODEL_SIZE`, `DEVICE` (`cuda`/`cpu`), `COMPUTE_TYPE`, `API_TOKEN`, plus the
decoding/VAD/backpressure knobs documented in the deploy unit's `.env.example`.

`STT_BACKEND` selects `whisper` (default, model loaded at startup) or `transcribe` (streaming PCM16, no model load).
For Transcribe, set `AWS_REGION` and provide credentials through the standard AWS chain
(environment, profile, or IAM). `/health` checks region configuration and credential resolution.
`TRANSCRIBE_LANGUAGE_DEFAULT` and `TRANSCRIBE_TIMEOUT_S` optionally override the backend defaults;
the timeout must be positive and below the bot client's 30-second deadline.
Run `deploy/transcription/docker-compose.transcribe.yml` for a single CPU-container worker;
it publishes the API directly and forwards environment credentials, including optional session tokens.
The response retains the Whisper `verbose_json` contract, with optional word timestamps and no speaker labels.
