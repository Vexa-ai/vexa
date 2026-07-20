# deploy/transcription — the STT service, deployed separately

The transcription service ([`core/meetings/services/transcription`](../../core/meetings/services/transcription))
is the **GPU workload, carved out** of the main stack. It is **not** part of `make all`: the main
`deploy/compose` stack runs CPU-only anywhere, and reaches transcription over the network via
`TRANSCRIPTION_SERVICE_URL`. Stand this unit up wherever a GPU lives (the same host or a dedicated
GPU box).

## Why separate

GPU inference is expensive, stateful, and hardware-specific. Forcing it into `make all` would make
the quickstart require an NVIDIA GPU. Keeping it as its own deploy unit lets the core stack run
everywhere, and lets the GPU tier scale (add workers) and relocate independently.

## Run

```bash
cp .env.example .env          # set MODEL_SIZE, API_TOKEN, TRANSCRIPTION_LB_PORT
docker compose up -d          # GPU (nvidia-container-toolkit required)
# or, no GPU:
docker compose -f docker-compose.cpu.yml up -d

docker compose logs -f        # wait for "Model loaded successfully"
curl http://localhost:8083/health
```

The nginx LB publishes port `8083` (`TRANSCRIPTION_LB_PORT`); it least-conn balances across
workers. Ships with one worker — scale out by uncommenting `transcription-worker-2/3` in
`docker-compose.yml` **and** `nginx.conf` (one GPU each).

## Point the main stack at it

In the main stack's `deploy/compose/.env`, set the **base URL** (the bot's whisper client appends
`/v1/audio/transcriptions`):

| Topology | `TRANSCRIPTION_SERVICE_URL` |
|---|---|
| Dedicated GPU host | `http://<gpu-host>:8083` |
| Same Docker host as the stack | `http://<host-LAN-ip>:8083` (or attach both to a shared external docker network and use `http://transcription-api`) |

If `API_TOKEN` is set here, set the **same** value as `TRANSCRIPTION_SERVICE_TOKEN` in the main
stack's `.env`. Then bots transcribe end-to-end: bot → this service → segments → meeting-api
`collector` → `transcription_segments` → live fan-out.

**This unit ignores the request's `model` form part** (OpenAI-compat: the field is required, the
server decides): the model that actually runs is this unit's own `MODEL_SIZE`. So the main stack's
`TRANSCRIPTION_MODEL` has no effect here — it exists for backends that *validate* the model id
(Groq, vLLM, OpenAI-compatible gateways); leave it empty when pointing at this unit and pick the
model with `MODEL_SIZE` instead.

Downloaded model weights live in the `transcription-models` **named volume** (not the working
tree), persisted across restarts. Wipe with `docker volume rm transcription_transcription-models`.
