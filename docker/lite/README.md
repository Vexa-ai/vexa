# Vexa Lite Deployment

All-in-one Docker deployment for platforms without Docker socket access (EasyPanel, Dokploy, Railway, Render, etc.).

**Note:** This deployment includes Redis server inside the container. Only PostgreSQL needs to be provided externally.

## Quick Start

```bash
# Build the image
docker build -f Dockerfile.lite -t vexa-lite .

# Run with internal Redis & external PostgreSQL (default: remote transcription)
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/vexa" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  -e REMOTE_TRANSCRIBER_URL="http://localhost:8083/v1/audio/transcriptions" \
  -e REMOTE_TRANSCRIBER_API_KEY="your-api-key" \
  vexa-lite
```

**API Access:** 
- API Gateway: `http://localhost:8056/docs` (includes Admin API routes at `/admin/*` and MCP at `/mcp`)

**Notes:**
- Redis runs internally on `localhost:6379` by default. To use an external Redis, set `REDIS_HOST` to your Redis server address.
- Default transcription mode is `remote` - WhisperLive calls an external transcription service via `REMOTE_TRANSCRIBER_URL` and `REMOTE_TRANSCRIBER_API_KEY`.
- If transcription service uses Docker service names (e.g., `transcription-lb`), add `--network transcription-network` to the docker run command.
- If transcription service is accessible via host port (e.g., `localhost:8083`), no network flag needed.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Lite Container                         │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌──────┐ │
│  │ API Gateway │  │  Admin API  │  │ Bot Manager  │  │ MCP  │ │
│  │   :8056     │  │    :8057    │  │    :8080     │  │:18888│ │
│  │  (external) │  │  (internal) │  │  (internal)  │  │(int.)│ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘  └──┬───┘ │
│         │                │                 │             │     │
│         └────────────────┴─────────────────┴─────────────┘     │
│                    (routes: /admin/*, /mcp, /bots, /transcripts)│
│                                           │                     │
│                                    spawns processes             │
│                                           ↓                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Bot Processes (Node.js/Playwright)          │   │
│  │         bot-1 (pid)    bot-2 (pid)    bot-3 (pid)       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                          │                                      │
│                     audio stream                                │
│                          ↓                                      │
│  ┌─────────────────┐           ┌─────────────────────────┐     │
│  │   WhisperLive   │──Redis───▶│ Transcription Collector │     │
│  │     :9090       │  Stream   │         :8123           │     │
│  └────────┬────────┘           └─────────────────────────┘     │
│           │                                                      │
│           │ (calls remote transcription service)                 │
│           │                                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Xvfb (:99)                            │   │
│  │              Virtual Display for Browsers                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Redis Server                          │   │
│  │                    :6379 (internal)                    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                    │              │
                    ▼              ▼
             ┌──────────────┐  ┌──────────┐
             │ Transcription │  │ Postgres │
             │   Service     │  │(external)│
             │   (remote)     │  │          │
             └──────────────┘  └──────────┘
```

**Key difference from standard deployment:** Instead of spawning Docker containers for bots, the Lite version uses a **process orchestrator** that spawns bots as Node.js child processes within the same container.

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection URL | `postgresql://user:pass@host:5432/vexa` |
| `ADMIN_API_TOKEN` | Secret token for admin operations | `your-secret-token-here` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `localhost` | Redis host (use `localhost` for internal Redis, or external hostname) |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_URL` | Auto-generated | Full Redis URL (auto-generated from host/port if not provided) |
| `DEVICE_TYPE` | `remote` | Device type: `remote` (default), `cpu` (for local faster-whisper) |
| `WHISPER_BACKEND` | `remote` | WhisperLive backend: `remote` (default), `faster_whisper` (for local CPU) |
| `WHISPER_MODEL_SIZE` | `tiny` | Whisper model size (only used for `faster_whisper` backend) |
| `LOG_LEVEL` | `info` | Logging level (debug, info, warning, error) |
| `REMOTE_TRANSCRIBER_URL` | (required) | Remote transcription API URL |
| `REMOTE_TRANSCRIBER_API_KEY` | (required) | API key for remote transcription service |
| `REMOTE_TRANSCRIBER_TEMPERATURE` | `0` | Temperature parameter for remote transcription |
| `SKIP_TRANSCRIPTION_CHECK` | `false` | If `true`, skips the startup connectivity check to the transcription service. **Only relevant when using an external (non‑Vexa) Whisper‑compatible transcription service** that does not expose a `/health` or root URL returning 2xx (e.g. some third‑party/OVH gateways). The Vexa transcription service (developed in this stack) exposes `/health` and does not need this. The container still requires `REMOTE_TRANSCRIBER_URL` and `REMOTE_TRANSCRIBER_API_KEY` to be set. |
| `API_GATEWAY_URL` | `http://localhost:8056` | API Gateway URL (used by MCP service) |
| `STORAGE_BACKEND` | `local` | Recording storage backend: `local`, `minio`, or `s3` |
| `LOCAL_STORAGE_DIR` | `/var/lib/vexa/recordings` | Local recording directory when `STORAGE_BACKEND=local` |
| `LOCAL_STORAGE_FSYNC` | `true` | If `true`, fsync recording writes for durability |
| `MINIO_ENDPOINT` | (empty) | MinIO/S3-compatible endpoint when `STORAGE_BACKEND=minio` |
| `MINIO_ACCESS_KEY` | (empty) | Access key for MinIO backend |
| `MINIO_SECRET_KEY` | (empty) | Secret key for MinIO backend |
| `MINIO_BUCKET` | `vexa-recordings` | Bucket for MinIO backend |
| `MINIO_SECURE` | `false` | Use TLS for MinIO endpoint |
| `AWS_REGION` | `us-east-1` | Region for `s3` backend |
| `AWS_ACCESS_KEY_ID` | (empty) | AWS access key for `s3` backend |
| `AWS_SECRET_ACCESS_KEY` | (empty) | AWS secret key for `s3` backend |
| `S3_BUCKET` | (empty) | Bucket for `s3` backend |
| `S3_ENDPOINT` | (empty) | Optional custom endpoint for S3-compatible providers |
| `S3_SECURE` | `true` | Use TLS for `S3_ENDPOINT` |

### External transcription services without health endpoint

At startup, the Lite entrypoint verifies that the transcription service is reachable by requesting `BASE_URL/health` (or the root URL if that fails). It uses `curl -f`, so any HTTP 4xx or 5xx (e.g. **404**) causes the check to fail and the container to exit with "Cannot reach transcription service".

This applies **only when you use an external (outside) Whisper‑compatible transcription service**—not the Vexa transcription service developed in this stack, which exposes `/health` and works with the default check. If your **external** service does not expose a `/health` or root endpoint that returns 2xx (e.g. some third‑party or proxy gateways that only serve the transcription path), set:

```bash
-e SKIP_TRANSCRIPTION_CHECK=true
```

With this set, the entrypoint only checks that `REMOTE_TRANSCRIBER_URL` (or `TRANSCRIBER_URL`) and `REMOTE_TRANSCRIBER_API_KEY` (or `TRANSCRIBER_API_KEY`) are non-empty; it does not perform the connectivity or API-key request.

### Redis Configuration

**Internal Redis (Default):**
- Redis server runs inside the container on `localhost:6379`
- No configuration needed - works out of the box
- Data persists in `/var/lib/redis` (use volumes for persistence)

**External Redis:**
```bash
# Use external Redis by setting REDIS_HOST
-e REDIS_HOST=redis.example.com
-e REDIS_PORT=6379
-e REDIS_PASSWORD=your-password  # Optional
```

### Alternative Configuration (Individual Variables)

Instead of URLs, you can use individual variables:

```bash
# Database
DB_HOST=postgres.example.com
DB_PORT=5432
DB_NAME=vexa
DB_USER=postgres
DB_PASSWORD=your-password
DB_SSL_MODE=disable  # Use "disable" for local PostgreSQL

# Redis (only needed for external Redis)
REDIS_HOST=redis.example.com
REDIS_PORT=6379
REDIS_PASSWORD=your-redis-password
```

## Whisper Model Selection

| Model | Size | Quality | Speed | Recommended For |
|-------|------|---------|-------|-----------------|
| `tiny` | ~75MB | Basic | Fast | Development, testing |
| `small` | ~500MB | Good | Medium | Light production |
| `medium` | ~1.5GB | Better | Slower | Production |
| `large` | ~3GB | Best | Slowest | High-quality requirements |

```bash
# Example: Use CPU mode with local faster-whisper (instead of remote)
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DEVICE_TYPE=cpu \
  -e WHISPER_BACKEND=faster_whisper \
  -e WHISPER_MODEL_SIZE=medium \
  -e DATABASE_URL="..." \
  -e ADMIN_API_TOKEN="..." \
  vexa-lite
```

**Note:** 
- Default mode is `remote` transcription (requires `REMOTE_TRANSCRIBER_URL` and `REMOTE_TRANSCRIBER_API_KEY`)
- For local CPU transcription, set `DEVICE_TYPE=cpu` and `WHISPER_BACKEND=faster_whisper`
- Models are downloaded on first use for local CPU mode. Larger models require more RAM and CPU.

## Persistent Storage (Volumes)

For production deployments, mount volumes to persist data:

```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -v vexa-logs:/var/log/vexa-bots \
  -v vexa-recordings:/var/lib/vexa/recordings \
  -e DATABASE_URL="..." \
  -e ADMIN_API_TOKEN="..." \
  -e REMOTE_TRANSCRIBER_URL="http://localhost:8083/v1/audio/transcriptions" \
  -e REMOTE_TRANSCRIBER_API_KEY="your-api-key" \
  vexa-lite
```

| Volume | Path | Description |
|--------|------|-------------|
| `vexa-logs` | `/var/log/vexa-bots` | Bot process logs |
| `vexa-recordings` | `/var/lib/vexa/recordings` | Persisted recording files for `STORAGE_BACKEND=local` |

**Note:** Model volumes are only needed for local CPU mode (`WHISPER_BACKEND=faster_whisper`). Remote transcription mode doesn't require model storage.

### Recording backend quick switch

Local filesystem (default in Lite):

```bash
-e STORAGE_BACKEND=local \
-e LOCAL_STORAGE_DIR=/var/lib/vexa/recordings \
-e LOCAL_STORAGE_FSYNC=true
```

Cloud object storage (AWS S3):

```bash
-e STORAGE_BACKEND=s3 \
-e AWS_REGION=us-east-1 \
-e AWS_ACCESS_KEY_ID=... \
-e AWS_SECRET_ACCESS_KEY=... \
-e S3_BUCKET=vexa-recordings
```

S3-compatible providers can also set:

```bash
-e S3_ENDPOINT=https://<provider-endpoint> \
-e S3_SECURE=true
```

## Platform-Specific Deployment

### EasyPanel

1. Create a new **App** from Git repository or Docker image
2. Expose port: `8056` (API Gateway - routes all services including Admin API and MCP)
3. Configure environment variables:
   - `DATABASE_URL` → Use EasyPanel PostgreSQL service URL
   - `ADMIN_API_TOKEN` → Generate a secure token
   - `REMOTE_TRANSCRIBER_URL` → Your transcription service URL (e.g., `http://transcription-service.example.com/v1/audio/transcriptions`)
   - `REMOTE_TRANSCRIBER_API_KEY` → Your transcription service API key
4. Optional: Add persistent volumes for logs

### Dokploy

1. Create a new **Application** → Docker deployment
2. Use `Dockerfile.lite` or pre-built image
3. Expose port: `8056` (API Gateway - routes all services including Admin API and MCP)
4. Set environment variables in Dokploy's env section:
   - `DATABASE_URL` → PostgreSQL service URL
   - `ADMIN_API_TOKEN` → Generate a secure token
   - `REMOTE_TRANSCRIBER_URL` → Your transcription service URL (public URL or Docker service name)
   - `REMOTE_TRANSCRIBER_API_KEY` → Your transcription service API key
5. Configure PostgreSQL service in Dokploy

### Railway / Render

1. Deploy from GitHub with `Dockerfile.lite`
2. Set exposed port: `8056` (API Gateway - routes all services including Admin API and MCP)
3. Add PostgreSQL as managed service
4. Configure environment variables:
   - `DATABASE_URL` → PostgreSQL service URL
   - `ADMIN_API_TOKEN` → Generate a secure token
   - `REMOTE_TRANSCRIBER_URL` → Your transcription service URL (public URL)
   - `REMOTE_TRANSCRIBER_API_KEY` → Your transcription service API key

## Management

### View Logs

```bash
# All services (stdout)
docker logs vexa

# Follow logs
docker logs -f vexa

# Specific service logs (inside container)
docker exec vexa cat /var/log/supervisor/api-gateway.log
docker exec vexa cat /var/log/supervisor/bot-manager.log
docker exec vexa cat /var/log/supervisor/whisperlive.log
```

### Service Status

```bash
docker exec vexa supervisorctl status
```

Output:
```
vexa-core:admin-api              RUNNING   pid 123, uptime 0:05:00
vexa-core:api-gateway            RUNNING   pid 124, uptime 0:05:00
vexa-core:bot-manager            RUNNING   pid 125, uptime 0:05:00
vexa-core:transcription-collector RUNNING   pid 126, uptime 0:05:00
vexa-core:whisperlive            RUNNING   pid 127, uptime 0:05:00
vexa-core:xvfb                   RUNNING   pid 128, uptime 0:05:00
vexa-core:mcp                    RUNNING   pid 129, uptime 0:05:00
```

### Restart a Service

```bash
docker exec vexa supervisorctl restart vexa-core:whisperlive
docker exec vexa supervisorctl restart vexa-core:bot-manager
```

## Testing

### Create a User and Get API Key

```bash
# Create user (via Admin API through API Gateway)
curl -X POST "http://localhost:8056/admin/users" \
  -H "X-Admin-API-Key: your-admin-token" \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "name": "Test User"}'

# Response includes user info:
# {"id": 1, "email": "test@example.com", "name": "Test User", ...}

# Generate API token for the user
curl -X POST "http://localhost:8056/admin/users/1/tokens" \
  -H "X-Admin-API-Key: your-admin-token"

# Response includes API key:
# {"user_id": 1, "id": 1, "token": "vx_abc123...", ...}
```

### Start a Bot

```bash
curl -X POST "http://localhost:8056/bots" \
  -H "X-API-Key: vx_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "bot_name": "Vexa Bot",
    "language": "en"
  }'
```

### Get Transcription

```bash
curl "http://localhost:8056/transcripts/google_meet/abc-defg-hij" \
  -H "X-API-Key: vx_abc123..."
```

### Using MCP Service (Model Context Protocol)

The MCP service provides a Model Context Protocol interface for Claude Desktop, Cursor, and other MCP-compatible clients. The MCP service is accessible through the API Gateway.

**Configure Claude Desktop:**

1. Open Claude Desktop Settings → Developer → Edit Config
2. Add the following configuration:

```json
{
  "mcpServers": {
    "Vexa": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://localhost:8056/mcp",
        "--header",
        "Authorization:${VEXA_API_KEY}"
      ],
      "env": {
        "VEXA_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

3. Replace `your-api-key-here` with your Vexa API key
4. Restart Claude Desktop

**For remote deployments**, replace `http://localhost:8056/mcp` with your public gateway URL (e.g., `https://vexa-lite.fly.dev/mcp`).

**MCP Endpoints:**
- MCP Protocol: `http://localhost:8056/mcp` (via API Gateway)
- All MCP requests are routed through the API Gateway on port 8056

See `services/mcp/README.md` for detailed MCP setup instructions.

## Comparison with Standard Deployment

| Feature | Standard (Docker Compose) | Lite |
|---------|---------------------------|------------|
| **Services** | Multiple containers | Single container |
| **Bot Spawning** | Docker containers | Node.js processes |
| **Docker Socket** | Required | Not required |
| **Traefik/Consul** | Included | Not needed |
| **Redis** | External container | Internal (included) |
| **PostgreSQL** | External container | External (required) |
| **Transcription** | GPU/CPU/Remote | Remote (default) or CPU |
| **GPU Support** | Yes | No (uses remote transcription service) |
| **Scaling** | Horizontal | Vertical |
| **Max Concurrent Bots** | Unlimited* | 3-5 recommended |
| **Complexity** | Higher | Lower |
| **Use Case** | Production, self-hosted | PaaS, simple deployments |

## Limitations

- **Remote Transcription Default:** Uses remote transcription service (requires `REMOTE_TRANSCRIBER_URL` and `REMOTE_TRANSCRIBER_API_KEY`)
- **Local CPU Mode:** Available but slower - set `DEVICE_TYPE=cpu` and `WHISPER_BACKEND=faster_whisper`
- **Concurrent Bots:** Recommended max 3-5 (shared CPU/RAM)
- **Process Isolation:** Less isolated than container-per-bot
- **Model Size:** Only relevant for local CPU mode - larger models require more RAM and CPU
- **Redis Persistence:** Internal Redis data is ephemeral unless volumes are mounted

## Troubleshooting

### "Cannot reach transcription service" at startup

The entrypoint checks reachability by requesting the transcription service's `/health` (or root) URL. If that returns 404 or another non-2xx, the container exits with this error. This only affects **external** Whisper-compatible services that don't expose such an endpoint; the Vexa transcription service in this stack has `/health` and does not need a workaround. **Fix for external services:** set `SKIP_TRANSCRIPTION_CHECK=true`. See [External transcription services without health endpoint](#external-transcription-services-without-health-endpoint) above.

### Bot Fails to Start

```bash
# Check bot manager logs
docker logs vexa 2>&1 | grep -i "bot-manager"

# Verify Xvfb is running (required for browsers)
docker exec vexa supervisorctl status vexa-core:xvfb
```

### Transcriptions Not Appearing

```bash
# Check WhisperLive Redis connection
docker logs vexa 2>&1 | grep -i "redis"

# Verify Redis stream URL is set correctly
docker exec vexa env | grep REDIS
```

### High Memory Usage

- Use a smaller Whisper model (`tiny` or `small`)
- Limit concurrent bots
- Increase container memory limits

## Files

| File | Description |
|------|-------------|
| `Dockerfile.lite` | Main Dockerfile (in repo root) |
| `docker/lite/supervisord.conf` | Supervisor configuration |
| `docker/lite/entrypoint.sh` | Container initialization |
| `docker/lite/requirements.txt` | Python dependencies |
| `services/bot-manager/app/orchestrators/process.py` | Process orchestrator |
| `services/mcp/` | MCP service (Model Context Protocol) |

## Changes from Open Source Project

The Lite deployment adds the following without modifying core service code:

**New Files:**
- `Dockerfile.lite` - All-in-one container build
- `docker/lite/*` - Configuration files
- `services/bot-manager/app/orchestrators/process.py` - Process-based bot spawner

**Included Services:**
- MCP Service (`services/mcp/`) - Model Context Protocol service for Claude/Cursor integration

**Minimal Modifications:**
- `services/bot-manager/app/orchestrators/__init__.py` - Loads process orchestrator when `ORCHESTRATOR=process`
- `services/transcription-collector/config.py` - Added `REDIS_PASSWORD` support
- `services/transcription-collector/main.py` - Password parameter in Redis connection

All changes are **backwards compatible** and don't affect standard Docker Compose deployment.
