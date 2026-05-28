# Vexa Lite

Single Docker container with all Vexa services. Simplest way to self-host.

## Why

Everything runs in one container -- API, dashboard, bots, Redis, audio stack. No Docker Compose, no orchestration. `make lite` provisions PostgreSQL and configures the transcription API for you.

- One container instead of 10+
- Full API + dashboard + meeting bots
- Concurrent bots scale with machine resources
- No GPU required -- transcription runs via external API

## Quick start

From repo root:

```bash
make lite
```

That's it. Provisions PostgreSQL, pulls the Vexa Lite image, starts everything, and verifies connectivity. Prompts for a transcription token on first run (get one at [vexa.ai/account](https://vexa.ai/account)).

After it finishes:

- **Dashboard:** `http://YOUR_IP:3000`
- **API docs:** `http://YOUR_IP:8056/docs`

To stop: `make lite-down`

## What's inside

14 services managed by supervisord:


| Service     | Port     | Description                                        |
| ----------- | -------- | -------------------------------------------------- |
| API Gateway | 8056     | Main entry point                                   |
| Admin API   | 8057     | User/token management                              |
| Meeting API | 8080     | Bot orchestration, transcription pipeline           |
| Runtime API | 8090     | Process lifecycle (spawns bots as child processes) |
| Agent API   | 8100     | AI agent chat runtime                              |
| Dashboard   | **3000** | Next.js web UI                                     |
| MCP         | 18888    | Model Context Protocol server                      |
| TTS         | 8059     | Text-to-speech (Piper, local)                      |
| Redis       | 6379     | Internal pub/sub and session state                 |
| Xvfb        | :99      | Virtual display for headless Chrome                |


### Architecture

```
+----------------------------------------------------------------+
|                      Lite Container                             |
|                                                                 |
|  Dashboard  API Gateway  Admin API  Meeting API  Runtime API    |
|   :3000       :8056        :8057      :8080       :8090         |
|                                                                 |
|  Agent API  TTS Service  MCP Server  Redis  Xvfb  PulseAudio   |
|   :8100       :8059       :18888     :6379  :99                 |
|                                                                 |
|  Bot Processes (Node.js/Playwright, spawned as child processes) |
+----------------------------------------------------------------+
         |                    |
         v                    v
   Transcription         PostgreSQL
     Service              (external)
```

Bots run as child processes inside the container (process backend), sharing Xvfb and PulseAudio. In [compose mode](../compose/README.md), each bot gets its own Docker container.

## Browser surface

Only **two** ports are part of the documented browser surface:

| Port | Service | Browser-facing |
|---|---|---|
| 3000 | Dashboard (Next.js) | Yes — this is what users open. |
| 8056 | API Gateway | Yes — but the dashboard prefers to proxy through itself; see below. |

The other 12 services (Admin :8057, Meeting :8080, Runtime :8090, Agent :8100, MCP :18888, TTS :8059, Redis :6379, Xvfb :99, PulseAudio, postgres-client, bot processes, supervisord) are **container-internal** in intent. They run on host ports today only because `make lite` uses `docker run --network host` for fast localhost wiring; nothing in the browser surface depends on them being reachable from outside the container, and a future hardened Lite is free to switch to bridged networking with only 3000 + 8056 published.

### How the browser is routed (dashboard runtime config)

The dashboard's `/api/config` route is the runtime SSOT for what URL the browser is told to talk to. It is computed by `resolveBrowserApiUrl()` in `services/dashboard/src/lib/browser-api-url.ts`. In Lite specifically:

- `VEXA_API_URL=http://localhost:8056` (set by `supervisord.conf`) is the *internal* gateway URL. Reaching it from the browser requires that the user can hit `localhost:8056` directly — which is only true on the same machine, not from any tunnel/proxy/sandbox.
- When the browser is at the dashboard's host port (e.g. `localhost:3000` or whatever the user mapped `:3000` to) the resolver detects both the configured public URL **and** the request host are loopback, and falls back to same-origin (`apiUrl=""`, `publicApiUrl=""`).
- The browser then uses the dashboard's compiled-in `/ws` rewrite (to `${VEXA_API_URL}/ws`) and `/api/vexa/*` proxy. Both terminate at the dashboard origin, so WebSocket upgrades succeed even in single-port-exposed browser environments.

This routing was hardened by two regression fixes in 0.10.6.3 (commits `df87805` and `3566512`) after the original resolver leaked the loopback gateway URL into the browser-facing `wsUrl`.

### Cookie names (avoiding collisions with co-resident Compose)

Lite sets distinct auth cookie names so a Lite dashboard and a Compose dashboard on the same host do not clobber each other's sessions:

| Variable | Lite default (set by `make lite`) | Generic default |
|---|---|---|
| `VEXA_AUTH_COOKIE_NAME` | `vexa-token-lite` | `vexa-token` |
| `VEXA_USER_INFO_COOKIE_NAME` | `vexa-user-info-lite` | `vexa-user-info` |

`/api/config` reads the auth cookie through this SSOT. Other dashboard cookie reads/writes are tracked for follow-on in [#382](https://github.com/Vexa-ai/vexa/issues/382).

## Configuration

Edit `.env` at repo root. Created automatically on first `make lite`.

### Required (prompted interactively)


| Variable                      | Description                         |
| ----------------------------- | ----------------------------------- |
| `TRANSCRIPTION_SERVICE_TOKEN` | API token for transcription service |


### Optional


| Variable            | Default                    | Description                       |
| ------------------- | -------------------------- | --------------------------------- |
| `ADMIN_TOKEN`       | `changeme`                 | Admin API authentication token    |
| `IMAGE_TAG`         | `latest`                   | Docker image tag to pull          |
| `STORAGE_BACKEND`   | `local`                    | `local`, `minio`, or `s3`        |
| `LOCAL_STORAGE_DIR` | `/var/lib/vexa/recordings` | Path for local storage            |
| `MINIO_ENDPOINT`    | --                         | MinIO host:port for S3 storage    |
| `MINIO_ACCESS_KEY`  | --                         | MinIO access key                  |
| `MINIO_SECRET_KEY`  | --                         | MinIO secret key                  |
| `LOG_LEVEL`         | `info`                     | Logging level for all services    |
| `OPENAI_API_KEY`    | --                         | Optional. Only consumed when `/speak` callers pass `provider=openai`. Default `provider=piper` runs locally with no key. |
| `PIPER_VOICES_DIR`  | `/app/voices`              | Where Piper TTS caches downloaded voice models (mount a volume to persist across restarts). |
| `PIPER_DEFAULT_VOICES` | `major` | Voices prepared on startup. `major` expands to the supported release-gate language set; other languages auto-download on first use via `provider=piper` auto-language detection. |
| `PIPER_LOAD_VOICES` | `en_US-amy-medium,en_US-danny-low,pt_BR-faber-medium,es_ES-davefx-medium` | Prepared voices also kept loaded in memory for the hottest release-gate languages. |
| `PIPER_PRELOAD_STRICT` | `true` | Fail startup when a configured voice cannot be prepared, so `/speak` does not accept traffic before promised voices are prompt. |


## Debugging

```bash
# Service health
docker logs vexa-lite 2>&1 | grep -A15 "Post-Startup Health"

# Supervisor status (all 14 services)
docker exec vexa-lite supervisorctl status

# Restart a single service
docker exec vexa-lite supervisorctl restart meeting-api

# Running bot processes
docker exec vexa-lite ps aux | grep "node dist/docker.js"

# Container logs
docker logs -f vexa-lite
```

## Lite vs. Compose


| Feature         | Lite                        | Compose                    |
| --------------- | --------------------------- | -------------------------- |
| Bot isolation   | Shared process space        | Separate Docker containers |
| Concurrent bots | Scales with machine resources | Scales with machine resources |
| Dashboard port  | 3000                        | 3001                       |
| Redis           | Internal (ephemeral)        | Configurable               |
| Scaling         | Single machine              | Multiple hosts             |
| Setup           | `make lite`                 | `make all`                 |


If you outgrow lite, switch to [compose](../compose/README.md).

## Known Issues


| Issue                       | Workaround                                                |
| --------------------------- | --------------------------------------------------------- |
| Zombie bot processes        | Check `ps aux` for zombies; restart container if needed   |
| CDP proxy port mismatch     | Connect to CDP on port 9222 directly (bypass gateway)     |
| Shared Chrome instance      | Run one browser session at a time                         |
| Redis ephemeral             | Mount `/var/lib/redis` as a volume if persistence needed  |
| PulseAudio loopback         | Use compose for multi-bot TTS tests                       |
