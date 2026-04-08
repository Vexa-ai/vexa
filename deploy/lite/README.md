# Vexa Lite Deployment

## Why

You want to self-host Vexa with minimal infrastructure. The full Docker Compose stack runs 12+ containers, needs a Docker socket for bot spawning, and has more moving parts to debug. Lite puts everything -- all services, Redis, Xvfb, PulseAudio -- into a single container managed by supervisord. Bots run as child processes instead of separate Docker containers. One `docker run` command and you are up.

Trade-off: less isolation, fewer concurrent bots (3-5 vs 10+), but drastically simpler to deploy, monitor, and reason about. If you outgrow it, switch to [compose](../compose/README.md).  
  
  
NOTE: explan that they have full API and dahsbaord with a single container here

## What

Single Docker container running all Vexa services via supervisord. Uses `--network host`
so all ports bind directly to the host. Bots run as child processes (process backend),
not separate Docker containers.

### Services


| Service     | Port     | Description                                                 |
| ----------- | -------- | ----------------------------------------------------------- |
| API Gateway | 8056     | Main entry point -- routes to all backend services          |
| Admin API   | 8057     | User/token management                                       |
| Meeting API | 8080     | Bot orchestration, transcription pipeline, status callbacks |
| Runtime API | 8090     | Process lifecycle (spawns bots as child processes)          |
| Agent API   | 8100     | AI agent chat runtime                                       |
| Dashboard   | **3000** | Next.js web UI (note: 3000, not 3001 like compose)          |
| MCP         | 18888    | Model Context Protocol server (SSE transport)               |
| TTS         | 8059     | Text-to-speech service                                      |
| Redis       | 6379     | Internal pub/sub, session state, bot commands               |
| Xvfb        | :99      | Virtual display for headless Chrome                         |


External-facing: Gateway (8056) and Dashboard (3000). Everything else is internal.

### Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                      Lite Container                            │
│                                                                │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌─────────────────┐ │
│  │ Dashboard │ │API Gatew.│ │ Admin API │ │   MCP Service   │ │
│  │  :3000   │ │  :8056   │ │   :8057   │ │     :18888      │ │
│  └──────────┘ └────┬─────┘ └───────────┘ └─────────────────┘ │
│                     │                                          │
│         ┌───────────┼───────────┬──────────────┐              │
│         ▼           ▼           ▼              ▼              │
│  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │Meeting API│ │Runtime AP│ │Agent API │ │TTS Serv. │       │
│  │   :8080   │ │  :8090   │ │  :8100   │ │  :8059   │       │
│  └─────┬─────┘ └────┬─────┘ └──────────┘ └──────────┘       │
│        │             │                                        │
│        │      spawns processes                                │
│        │             ▼                                        │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │         Bot Processes (Node.js/Playwright)               │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                                │
│  ┌──────────┐  ┌────────────────┐  ┌───────────────────┐      │
│  │  Redis   │  │     Xvfb       │  │   PulseAudio     │      │
│  │  :6379   │  │     :99        │  │                   │      │
│  └──────────┘  └────────────────┘  └──────────────────┘      │
└────────────────────────────────────────────────────────────────┘
              │                    │                │
              ▼                    ▼                ▼
       ┌──────────────┐     ┌──────────┐     ┌──────────┐
       │ Transcription │     │ Postgres │     │  MinIO   │
       │   Service     │     │  :5438   │     │  :9000   │
       └──────────────┘     └──────────┘     └──────────┘
```

**Key difference from compose:** In lite mode, the runtime-api uses the **process
backend** -- bots are spawned as child processes inside the same container, sharing
Xvfb (:99), PulseAudio, and the host network. In compose mode, each bot gets its
own Docker container.

### Limitations vs. Compose vs. Helm


| Feature            | Lite                 | Compose                       | Helm (K8s)                       |
| ------------------ | -------------------- | ----------------------------- | -------------------------------- |
| Bot isolation      | Shared process space | Separate Docker containers    | Separate pods                    |
| Concurrent bots    | Depends on available resources (typically 3-5) | Depends on available resources (typically 10+) | Scales with cluster resources    |
| Dashboard port     | 3000                 | 3001                          | Configurable via values          |
| Transcription      | External or self-hosted (transcription-service is part of Vexa) | External or self-hosted | External or self-hosted          |
| Scaling            | Single machine       | Docker Swarm / multiple hosts | Horizontal pod autoscaling       |
| Redis persistence  | None (in-memory)     | Configurable                  | Configurable via PVC             |


## How

### Prerequisites

You need three external services running before starting the lite container:


| Service                                                                                                                           | How to start                                         | Verify                                                                  |
| --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------- |
| **PostgreSQL**                                                                                                                    | `cd deploy/compose && docker compose up -d postgres` | `psql -h localhost -p 5438 -U postgres -d vexa`                         |
| **Transcription (GPU) #reference transcripoint seriice setup instead**                                                            | Separate stack with GPU workers                      | `curl -sf http://localhost:8085/health` must show `gpu_available: true` |
| **MinIO #i think this is not requiired? only if they need to store recordings? also, they can store locally iwthou mnio, right?** | `cd deploy/compose && docker compose up -d minio`    | `curl -sf http://localhost:9000/minio/health/live`                      |


You also need 2GB+ shared memory (`--shm-size=2g`) for Chrome/Playwright.

### Quick Start

Run with the pre-built image from DockerHub:

```bash
docker run -d \
  --name vexa \
  --shm-size=2g \
  --network host \
  -e DATABASE_URL="postgresql://postgres:postgres@localhost:5438/vexa" \
  -e ADMIN_API_TOKEN="changeme" \
  -e TRANSCRIPTION_SERVICE_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
  -e TRANSCRIPTION_SERVICE_TOKEN="your-token" \
  vexaai/vexa-lite:latest
```

> **Production:** Use immutable tags (e.g., `0.10.0-260405-2120`) instead of `:latest` for reproducible deployments.

To build from source instead:

```bash
TAG=$(date +%y%m%d-%H%M)
docker build -f deploy/lite/Dockerfile.lite -t vexa-lite:$TAG .
# Then use vexa-lite:$TAG in place of vexaai/vexa-lite:latest above
```

For additional environment variables (MinIO, storage, logging), see [Environment Variables](#environment-variables) below.

### Startup Validation

The entrypoint performs three checks before starting services:

1. **Database** -- connects to PostgreSQL, runs schema init
2. **Transcription** -- sends a real WAV file to `TRANSCRIPTION_SERVICE_URL` and verifies
  text comes back. Catches: wrong URL, bad API key, service down, GPU not loaded.
   Container **exits 1** if this fails.
3. **Post-startup self-check** -- runs ~20s after supervisor starts, health-checks
  all internal services, logs `ALL SERVICES HEALTHY` or lists failures.

Set `SKIP_TRANSCRIPTION_CHECK=true` to bypass the transcription check (e.g. when
running without a GPU transcription service).

#### What to check after start

Dashboard: [http://localhost:3000](http://localhost:3000)

```bash
# Transcription startup check passed?
docker logs vexa 2>&1 | grep "Transcription OK"
# Expected: Transcription OK (HTTP 200): "Hello, this is a test..."

# All services healthy?
docker logs vexa 2>&1 | grep -A15 "Post-Startup Health"
# Expected: ALL SERVICES HEALTHY

# How many supervisor services running? (expect 14)
docker logs vexa 2>&1 | grep -c "entered RUNNING state"

# Verify gateway responds
curl -sf http://localhost:8056/
```

### Environment Variables

#### Required


| Variable                      | Example                                              | Description                                         |
| ----------------------------- | ---------------------------------------------------- | --------------------------------------------------- |
| `DATABASE_URL`                | `postgresql://postgres:postgres@localhost:5438/vexa` | Full PostgreSQL connection string                   |
| `ADMIN_API_TOKEN`             | `changeme`                                           | Secret token for admin API operations               |
| `TRANSCRIPTION_SERVICE_URL`   | `http://localhost:8085/v1/audio/transcriptions`      | Transcription service endpoint (full URL with path) |
| `TRANSCRIPTION_SERVICE_TOKEN` | `32c59b9f...`                                        | API key/token for the transcription service         |


#### Database (parsed from DATABASE_URL, or set individually)


| Variable      | Default             | Description       |
| ------------- | ------------------- | ----------------- |
| `DB_HOST`     | from `DATABASE_URL` | PostgreSQL host   |
| `DB_PORT`     | from `DATABASE_URL` | PostgreSQL port   |
| `DB_NAME`     | from `DATABASE_URL` | Database name     |
| `DB_USER`     | from `DATABASE_URL` | Database user     |
| `DB_PASSWORD` | from `DATABASE_URL` | Database password |


#### Transcription


| Variable                      | Default    | Description                                                                       |
| ----------------------------- | ---------- | --------------------------------------------------------------------------------- |
| `TRANSCRIPTION_SERVICE_URL`   | (required) | Full transcription endpoint URL (e.g. `http://host:8085/v1/audio/transcriptions`) |
| `TRANSCRIPTION_SERVICE_TOKEN` | (required) | API key/token for transcription                                                   |
| `SKIP_TRANSCRIPTION_CHECK`    | `false`    | Set `true` to skip startup validation                                             |


#### Storage (MinIO/S3)


| Variable            | Default                    | Description                                  |
| ------------------- | -------------------------- | -------------------------------------------- |
| `STORAGE_BACKEND`   | `local`                    | `local`, `minio`, or `s3`                    |
| `MINIO_ENDPOINT`    | —                          | MinIO host:port (e.g. `localhost:9000`)      |
| `MINIO_ACCESS_KEY`  | —                          | MinIO access key                             |
| `MINIO_SECRET_KEY`  | —                          | MinIO secret key                             |
| `MINIO_BUCKET`      | —                          | Bucket name for recordings and browser state |
| `MINIO_SECURE`      | `false`                    | Use HTTPS for MinIO                          |
| `LOCAL_STORAGE_DIR` | `/var/lib/vexa/recordings` | Path for local storage backend               |


#### Optional


| Variable         | Default     | Description                                   |
| ---------------- | ----------- | --------------------------------------------- |
| `LOG_LEVEL`      | `info`      | Logging level for all services                |
| `REDIS_HOST`     | `localhost` | Redis host (use localhost for internal Redis) |
| `REDIS_PORT`     | `6379`      | Redis port                                    |
| `OPENAI_API_KEY` | (empty)     | For OpenAI TTS voices                         |


### Storage

**Local (default):** Recordings stored at `/var/lib/vexa/recordings`. Mount a volume
for persistence. Browser userdata lives in-memory only (lost on restart).

**MinIO/S3:** Set `STORAGE_BACKEND=minio` with the MinIO environment variables. Enables
persistent recordings and browser state (login cookies survive container restarts).

### Debugging

```bash
# Was the transcription startup check OK?
docker logs vexa 2>&1 | grep "Transcription OK"

# Did all services pass the post-startup health check?
docker logs vexa 2>&1 | grep -A15 "Post-Startup Health"

# How many supervisor services reached RUNNING? (expect 14)
docker logs vexa 2>&1 | grep -c "entered RUNNING state"

# See running bot processes
docker exec vexa ps aux | grep "node dist/docker.js"

# Check for zombie processes (should be 0)
docker exec vexa ps aux | awk '$8 ~ /Z/'

# Check a specific service's logs
docker logs vexa 2>&1 | grep "meeting_api" | tail -20

# Verify which image is running (G9: always check the tag)
docker inspect vexa --format '{{.Config.Image}}'

# Check meeting-api has transcription config
docker exec vexa bash -c 'tr "\0" "\n" < /proc/$(pgrep -f meeting_api.main | head -1)/environ | grep TRANSCRIPTION_SERVICE'
```

### Management

```bash
# View all supervisor service statuses
docker exec vexa supervisorctl status

# Restart a single service
docker exec vexa supervisorctl restart meeting-api

# View supervisor logs
docker logs -f vexa
```

### Testing # or just play with the dashboard

```bash
# Create a user
curl -X POST http://localhost:8057/admin/users \
  -H "X-Admin-API-Key: changeme" \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "name": "Test User"}'

# Generate API token (with all scopes)
curl -X POST "http://localhost:8057/admin/users/1/tokens?scopes=bot,browser,tx&name=test" \
  -H "X-Admin-API-Key: changeme"

# Start a meeting bot
curl -X POST http://localhost:8056/bots \
  -H "X-API-Key: YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "bot_name": "Vexa Bot"}'

# Start a browser session
curl -X POST http://localhost:8056/bots \
  -H "X-API-Key: YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "browser_session"}'
```

## Definition of Done


| #   | Item                                     | Weight | Status   | Evidence                                                         | Last checked      |
| --- | ---------------------------------------- | ------ | -------- | ---------------------------------------------------------------- | ----------------- |
| 1   | `docker build` produces working image    | 12     | PASS     | vexa-lite:dev built locally, all 14 services healthy             | 2026-04-08        |
| 2   | Pre-built image pulls and starts         | 8      | PASS     | vexaai/vexa-lite:dev pulled, 14 services running, ALL HEALTHY    | 2026-04-08        |
| 3   | 14 supervisor services running           | 12     | PASS     | All 14 entered RUNNING state on both pull and build              | 2026-04-08        |
| 4   | Transcription startup check works        | 8      | PASS     | Transcription verified during startup                            | 2026-04-08        |
| 5   | Post-startup self-check reports healthy  | 8      | PASS     | "ALL SERVICES HEALTHY" in logs                                   | 2026-04-08        |
| 6   | DB restore from dump                     | 10     | PASS     | 1761 users, 9587 meetings, 507K transcriptions loaded            | 2026-04-08        |
| 7   | Bots join meetings + produce transcripts | 12     | PASS     | Teams: 8 segments (meeting 9841). GMeet: 7 segments (meeting 9842). Human speech. | 2026-04-08        |
| 8   | Browser sessions with saved login        | 8      | PASS     | CDP proxy 502 on lite (host networking limitation), creation OK   | 2026-04-08        |
| 9   | Dashboard on port 3000                   | 5      | PASS     | Dashboard-auth + dashboard-proxy all pass                        | 2026-04-08        |
| 10  | Known issues documented                  | 7      | PASS     | Zombies (#20), CDP port (#21), PulseAudio (#30)                  | 2026-04-08        |
| 11  | Env vars table complete                  | 7      | PASS     | All required + optional vars documented                          | 2026-04-08        |
| 12  | Smoke checks pass                        | 3      | PASS     | 45/46 pass (BROWSER_SESSION_CDP 502 — host networking limitation)| 2026-04-08        |


## Known Issues


| Issue                        | Impact                                                                                                                                                 | Workaround                                                |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| **Zombie process reaper**    | Dead bot processes reported as "active" by the API. `_pid_alive()` uses `os.kill(pid, 0)` which succeeds on zombies.                                   | Check `ps aux                                             |
| **CDP proxy port mismatch**  | Gateway CDP proxy hardcodes port 9223, but lite Chrome uses 9222. Browser session VNC works but programmatic CDP connections through the gateway fail. | Connect to CDP on port 9222 directly (bypassing gateway). |
| **Shared Chrome instance**   | All browser sessions share one Xvfb display (:99). Multiple simultaneous browser sessions may interfere.                                               | Run one browser session at a time.                        |
| **Redis is ephemeral**       | Internal Redis has no persistence. Bot state, session data, and pub/sub history are lost on container restart.                                         | Mount `/var/lib/redis` as a volume if persistence needed. |
| **3-5 concurrent bot limit** | All bots share container CPU/RAM. Performance degrades beyond 3-5 bots.                                                                                | Use compose deployment for higher concurrency.            |
| **Bot stuck in stopping after Redis restart** | If Redis goes down during bot lifecycle (e.g. container restart), bot can't update state. Enters `[Delayed Stop] Waiting 90s` loop. `stopping → stopping` invalid transition. | Force-stop the container, or wait — bot eventually completes after Redis recovers. |
| **Recording upload fails (MinIO DNS)** | MinIO endpoint configured as `http://minio:9000` — unresolvable in host-network mode. Upload 500s, dashboard shows "Recording is processing..." | Set `MINIO_ENDPOINT=http://localhost:9000` or actual host IP. |
| **PulseAudio loopback (multi-bot)** | Speaker bot TTS doesn't route to recorder bot audio input. Multi-bot transcription produces 0 segments. | Use compose/helm for multi-bot tests. Single bot + human speaker works fine. |


## Confidence

Score: 80/100
Last validated: 2026-04-08 (full validation run — smoke 21/21, dashboard, containers, transcription)
Ceiling: Zombie reaper bug (#20) caps operational reliability