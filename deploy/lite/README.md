# Vexa Lite (v0.12)

The whole v0.12 control plane in **one container**. The simplest way to self-host — `make lite`
from the repo root provisions PostgreSQL + MinIO and runs everything else in a single image.

## Why

Everything except the datastores runs in one container — gateway, admin, meeting-api, runtime,
agent control plane, redis, and the X11/audio stack. No Docker socket, no per-service
containers. The runtime uses the **process backend**: meeting bots and agent workers run as
**child processes** inside the container, not socket-spawned containers.

- One app container instead of eight + on-demand workers
- Full API + terminal + meeting bots + agent
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

### Transcripts with no token and no GPU — `LOCAL_STT=1`

```bash
make -C deploy/lite up LOCAL_STT=1
```

Runs a bundled **faster-whisper CPU server on the English-only tiny model**
(`vexa-lite-whisper`) on the same network and **auto-wires `TRANSCRIPTION_SERVICE_URL`** to it —
real English transcripts out of the box, slower than a GPU but zero setup. This is also how a
**witness / human-eval box** comes up with transcription ready. Verify the basic STT path
(synthesize English speech → transcribe):

```bash
make -C deploy/lite stt-smoke        # ✓ local STT transcribes (model=whisper-1 → words)
```

The Makefile default `Systran/faster-whisper-tiny.en` and its existing accuracy example
`Systran/faster-whisper-small.en` are both English-only; neither can satisfy the Russian or
code-switch pilot. Start the bundled multilingual pilot backend explicitly with:

```bash
make -C deploy/lite up LOCAL_STT=1 WHISPER_MODEL=Systran/faster-whisper-small
```

`WHISPER_MODEL` is a Lite make variable, not an ALLOY flag. It chooses the model loaded by the
backend; it does not pin the language sent with an individual transcription request. Pair it with
`ALLOY_STT_LANGUAGE_MODE=auto`, which omits that request language and re-detects it on sequential
chunks separated by qualifying natural pauses. This recipe is the required backend setup for the
multilingual run, not evidence that clean-image product or Google Meet acceptance has passed. A
different compatible GPU image may still be supplied with `WHISPER_IMAGE=...`.
(The client sends `model=whisper-1`, the OpenAI id; faster-whisper-server accepts it and serves
`WHISPER_MODEL`.)

If the bundled third-party image's built-in `curl` self-probe is unusable, opt in to the equivalent
Python health probe. The option is applied only when Make creates `vexa-lite-whisper`; it does not
modify an existing container. Remove an existing container before the enable command so Make
recreates it with the override:

```bash
docker rm -f vexa-lite-whisper
ALLOY_STT_HEALTHCHECK=1 make -C deploy/lite up LOCAL_STT=1
```

This is a Make/ambient opt-in, not an `.env` setting: the Lite recipe deliberately does not import
it from `ENV_FILE`. Default/rollback `0` (or unset) leaves the image healthcheck unchanged.

After it finishes:

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
|  gateway  admin-api  meeting-api  runtime                    |
|   :8056     :8001      :8080       :8090                      |
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
| `TRANSCRIPTION_SERVICE_URL` / `_TOKEN` | — | STT endpoint + key, shared by the bot transcript pipeline and the terminal composer mic (dictation `/api/stt`). Unset → bots capture, no transcript; composer mic returns 503 "not configured" |
| `TRANSCRIPTION_MODEL` | — | STT model id sent on every request — required by backends that validate it (Groq `whisper-large-v3-turbo`, vLLM's served name). Unset → `whisper-1` |
| `ALLOY_STT_TELEMETRY` | `0` | ALLOY live STT queue telemetry in Redis/API/Terminal. Exact `1` registers the backend routes and enables publication/polling; unset, empty, `0`, or another value preserves the upstream-compatible path |
| `ADMIN_TOKEN` | `changeme` | admin API token (the stack's shared admin secret) |
| `IMAGE_TAG` | `latest` | the `vexaai/vexa-lite` tag to pull (a local `vexa-lite:dev` build wins) |

`make` variables (not `.env`) for the bundled local STT: `LOCAL_STT=1` (off by default),
`WHISPER_MODEL` (English-only default `Systran/faster-whisper-tiny.en`; multilingual pilot override
`Systran/faster-whisper-small`), `WHISPER_IMAGE`, `HOST_STT_PORT` (`8083`), and optional exact
`ALLOY_STT_HEALTHCHECK=1` (a Python self-probe; default/rollback `0`). When `LOCAL_STT=1`, the
bundled server overrides `TRANSCRIPTION_SERVICE_URL` for you.

### Opt-in ALLOY pilot profile

All seven `.env` pilot-profile switches preserve upstream behavior by default. The approved local
pilot overrides them explicitly; the values are configuration instructions, not proof about the
currently running image.

Runtime flags are inherited by Lite and newly spawned bot processes:

| Variable | Default | Approved pilot value | Rollback |
|---|---:|---:|---|
| `ALLOY_STT_MAX_CONCURRENCY` | `0` | `1` | Set `0` and restart Lite so new bots are unlimited by the ALLOY adapter |
| `ALLOY_STT_CHANNEL_BACKPRESSURE` | `0` | `1` | Set `0` and restart Lite so new bots use upstream scheduling |
| `ALLOY_STT_LANGUAGE_MODE` | `configured` | `auto` | Set `configured` and restart Lite so new bots forward the configured language |
| `ALLOY_STT_TELEMETRY` | `0` | `1` | Set `0` and restart Lite; routes, publication, and Terminal polling remain absent |

`ALLOY_STT_MAX_CONCURRENCY=1` limits each bot process independently; it is not a shared limit
across meetings, bot processes, or the Whisper service. `ALLOY_STT_LANGUAGE_MODE=auto` removes the
pinned language parameter, splits a window at qualifying natural pauses, and submits the chunks
sequentially inside one logical limiter/telemetry lifecycle. Results are merged onto the original
timeline; a mixed window reports `mul`. Pause-rich windows therefore cost additional inference
latency, and a switch without an acoustic pause remains backend-limited. For bundled local STT, the
multilingual pilot must also set the non-ALLOY make variable
`WHISPER_MODEL=Systran/faster-whisper-small`; the default `.en` model cannot satisfy that test.
Clean-image product and Google Meet acceptance remain pending.

Build flags are compiled into the image and require a rebuild after either change:

| Variable | Default | Approved pilot value | Rollback |
|---|---:|---:|---|
| `NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT` | `0` | `1` | Set `0` and rebuild to restore the upstream placeholder label |
| `ALLOY_SKIP_HF_CACHE_WARM` | `0` | `1` | Set `0` and rebuild to restore the upstream best-effort cache warm |
| `ALLOY_LITE_BUNDLED_PYTHON` | `0` | `1` | Set `0` and rebuild to restore the original `uv venv --python 3.12` managed-interpreter path |

`ALLOY_LITE_BUNDLED_PYTHON=1` exactly copies Python 3.12 from the pinned
`python:3.12-slim-bullseye` build stage before running the same five service-venv commands. This
avoids `uv`'s managed-Python download for the Lite build only. Unset, empty, `0`, or another value
does not select or build that fallback stage.

Put the explicit seven-line pilot profile in the repo-root `.env` (or the file selected with
`ENV_FILE=...`) before building and starting Lite:

```env
ALLOY_STT_MAX_CONCURRENCY=1
ALLOY_STT_CHANNEL_BACKPRESSURE=1
ALLOY_STT_LANGUAGE_MODE=auto
ALLOY_STT_TELEMETRY=1
NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT=1
ALLOY_SKIP_HF_CACHE_WARM=1
ALLOY_LITE_BUNDLED_PYTHON=1
```

`make -C deploy/lite build` and `push` read only the three named build flags from that file; a
non-empty make command-line or ambient value takes precedence, and an empty file value cannot
erase it. Those targets do not source the file or import any other key. Rebuild the image after
changing a build flag. `make -C deploy/lite up` resolves the four runtime flags with the same
precedence; rerun it after changing them so the recreated Lite container and newly spawned bot
processes inherit the profile.

Agent inference is BYO — point the runtime at your endpoint via `ANTHROPIC_*` / `VEXA_AGENT_MODEL`
in `.env`; the runtime brokers credentials into spawned workers (nothing leaves the network).

## Debugging

```bash
docker logs -f vexa-lite                          # container logs
docker exec vexa-lite supervisorctl status        # all supervised services
docker exec vexa-lite supervisorctl restart meeting-api
docker exec vexa-lite ps aux | grep dist/index.js # running bot processes
```

Only when `ALLOY_STT_TELEMETRY=1` exactly, the Terminal footer shows the owner-scoped ALLOY STT
monitor on every screen. Its compact line reports active STT requests, waiting channels, queued
audio seconds, maximum audio lag, the slowest current RTF, and server-computed health. Click it for
per-meeting values, superseded-window counts, update age, and the last worker error. Terminal polls
once per second only while the document is visible. Timer and visibility triggers reuse the
current request promise within one active polling generation. Stop/restart can start a fresh
request while the invalidated generation's old network call remains pending; generation fencing
ignores its result. A temporary gateway failure retains the last good snapshot.

The authenticated backend endpoint is `http://localhost:8056/alloy/stt/status`. The browser uses
the Terminal proxy at `http://localhost:3001/api/alloy/stt/status`. Meeting API reads snapshots only
for owner-owned active meetings; transcript or workspace sharing does not grant telemetry access.
`STT unavailable` means the current load is unknown, not that the queue is empty, and telemetry
failure does not stop transcription.

When the flag is disabled, Meeting API and Gateway do not register the telemetry route, and
Terminal keeps the upstream `reset layout` footer without creating a telemetry hook, subscription,
timer, or request.

Focused source and contract checks do not prove that a currently running image matches this source.
A clean Dockerfile build, image/source provenance check, disposable-Redis lanes, and real Google
Meet multilingual and queue-recovery acceptance remain separate evidence gates.

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

## Smoke probe — "is this install actually working?"

```bash
make probe SURFACE=lite          # from the repo root, against a running `make lite`
```

The full-journey smoke (spawn → schedule → boot → join → transcribe → live-view → stop + a
one-shot log sweep of the container and every bot workload log), driven through the published
gateway. Lite runs the real bot, so the dead-URL journey's truthful terminal is a NAMED
failure — never a fake green. See `deploy/lite/probe.sh`.
