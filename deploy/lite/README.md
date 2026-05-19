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
| `OPENAI_API_KEY`    | --                         | For OpenAI TTS voices (optional)  |


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
