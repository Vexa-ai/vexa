# Vexa Lite (v0.12)

The whole v0.12 control plane in **one container**. The simplest way to self-host — `make lite`
from the repo root provisions PostgreSQL + MinIO and runs everything else in a single image.

## Why

Everything except the datastores runs in one container — gateway, admin, meeting-api, runtime,
agent control plane, dashboard, redis, and the X11/audio stack. No Docker socket, no per-service
containers. The runtime uses the **process backend**: meeting bots and agent workers run as
**child processes** inside the container, not socket-spawned containers.

- One app container instead of eight + on-demand workers
- Full API + dashboard + meeting bots + agent
- No GPU required — transcription runs via an external API (or your own GPU service)

## Quick start

From the repo root:

```bash
make lite
```

Provisions a PostgreSQL + MinIO sidecar, pulls/builds the lite image, starts everything on the
host network, and probes the front doors. Set `TRANSCRIPTION_SERVICE_URL` /
`TRANSCRIPTION_SERVICE_TOKEN` in the repo-root `.env` for transcripts (get a token at
`vexa.ai/account`, or self-host the transcription service on a GPU).

After it finishes:

- **Dashboard:** `http://YOUR_IP:3000`
- **Terminal:** `http://YOUR_IP:3001` (the agent-domain browser-CLI workbench)
- **API:** `http://YOUR_IP:8056` (the gateway — auth, routing) · docs at `/docs`
- **Agent API:** `http://YOUR_IP:8100`

To stop: `make lite-down` (data volumes are kept; `docker volume rm vexa-lite-pgdata
vexa-lite-miniodata` to wipe).

## What's inside

Supervised by `supervisord`:

| Service | Port | Role |
|---|---|---|
| gateway | **8056** | the one front door — auth, scopes, routing, `/ws` fan-out |
| admin-api | 8001 | users + API keys + `/internal/validate` |
| meeting-api | 8080 | bots, transcripts, recordings (→ MinIO) |
| runtime | 8090 | spawns bot + agent workers as **child processes** (process backend) |
| agent-api | **8100** | the agent control plane — dispatch, chat (SSE), routines |
| dashboard | **3000** | Next.js web UI (meetings + transcripts) |
| terminal | **3001** | agent-domain browser-CLI workbench (Next.js + custom `server.mjs` SSE/`/ws` relay) |
| redis | 6379 | bus + scheduler + per-dispatch streams (internal) |
| Xvfb · fluxbox · PulseAudio | :99 | display + audio for the headful bot browser |
| x11vnc · noVNC | 5900 / 6080 | browser view (debugging) |

External (the `make lite` sidecars): **PostgreSQL** (metadata) and **MinIO** (recordings +
agent workspaces).

### Architecture

```
+--------------------------------------------------------------+
|                    Vexa Lite container                       |
|                                                              |
|  dashboard  gateway  admin-api  meeting-api  runtime         |
|   :3000      :8056     :8001      :8080       :8090           |
|                                                              |
|  agent-api   redis   Xvfb  fluxbox  PulseAudio  noVNC        |
|   :8100      :6379    :99                        :6080       |
|                                                              |
|  bot processes (Playwright)  +  agent workers (Claude Code)  |
|     ← runtime spawns as child processes (process backend)    |
+--------------------------------------------------------------+
        |                    |                    |
        v                    v                    v
   Transcription        PostgreSQL             MinIO
     (external)         (sidecar)             (sidecar)
```

In [compose mode](../compose/README.md) the runtime spawns each bot/agent in its **own
container** via the Docker socket; in lite they are child processes sharing one display/audio.

## Configuration

The repo-root `.env` (auto-seeded from `deploy/compose/.env` if present, else minimal):

| Variable | Default | Description |
|---|---|---|
| `TRANSCRIPTION_SERVICE_URL` / `_TOKEN` | — | STT endpoint + key. Unset → bots capture, no transcript |
| `ADMIN_TOKEN` | `changeme` | admin API token (the stack's shared admin secret) |
| `IMAGE_TAG` | `latest` | the `vexaai/vexa-lite` tag to pull (a local `vexa-lite:dev` build wins) |

Agent inference is BYO — point the runtime at your endpoint via `ANTHROPIC_*` / `VEXA_AGENT_MODEL`
in `.env`; the runtime brokers credentials into spawned workers (nothing leaves the network).

## Debugging

```bash
docker logs -f vexa-lite                          # container logs
docker exec vexa-lite supervisorctl status        # all supervised services
docker exec vexa-lite supervisorctl restart meeting-api
docker exec vexa-lite ps aux | grep dist/index.js # running bot processes
```

## Lite vs. Compose

| | Lite | Compose |
|---|---|---|
| Bot / agent isolation | POSIX (per-subject uid, 0700 tiers, per-share gids) | separate containers (per-mount binds) |
| Docker socket | not needed | required (runtime spawns over it) |
| Datastores | postgres + minio sidecars | in-stack |
| Setup | `make lite` | `make all` |

Outgrow lite? Switch to [compose](../compose/README.md) — same images, same contracts.

## Known limitations

| Issue | Note |
|---|---|
| Shared X11 display | bots share one Xvfb (`:99`) — best for one browser session at a time |
| Ephemeral redis | internal redis is in-container; mount `/var/lib/redis` for persistence |
| Agent ↔ gateway | the agent control plane is reached directly on `:8100` (gateway-fronting is roadmap) |
