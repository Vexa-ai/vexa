# Vexa Monolithic Deployment

All-in-one Docker deployment for platforms without Docker socket access (EasyPanel, Dokploy, Railway, Render, etc.).

## Quick Start

```bash
# Build the image
docker build -f Dockerfile.monolithic -t vexa-monolithic .

# Run with external Redis & PostgreSQL
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -p 8057:8057 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/vexa" \
  -e REDIS_URL="redis://:password@host:6379/0" \
  -e ADMIN_API_TOKEN="your-secret-admin-token" \
  vexa-monolithic
```

**Endpoints:**
- API Gateway: `http://localhost:8056/docs`
- Admin API: `http://localhost:8057/docs`

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Monolithic Container                         │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐            │
│  │ API Gateway │  │  Admin API  │  │ Bot Manager  │            │
│  │   :8056     │  │    :8057    │  │    :8080     │            │
│  └─────────────┘  └─────────────┘  └──────┬───────┘            │
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
│  └─────────────────┘           └─────────────────────────┘     │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Xvfb (:99)                            │   │
│  │              Virtual Display for Browsers                │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                    │                         │
                    ▼                         ▼
             ┌──────────┐              ┌──────────┐
             │  Redis   │              │ Postgres │
             │(external)│              │(external)│
             └──────────┘              └──────────┘
```

**Key difference from standard deployment:** Instead of spawning Docker containers for bots, the monolithic version uses a **process orchestrator** that spawns bots as Node.js child processes within the same container.

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection URL | `postgresql://user:pass@host:5432/vexa` |
| `REDIS_URL` | Redis connection URL | `redis://:password@host:6379/0` |
| `ADMIN_API_TOKEN` | Secret token for admin operations | `your-secret-token-here` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL_SIZE` | `tiny` | Whisper model size (see below) |
| `LOG_LEVEL` | `info` | Logging level (debug, info, warning, error) |
| `DEVICE_TYPE` | `cpu` | Device type (cpu only in monolithic) |

### Alternative Configuration (Individual Variables)

Instead of URLs, you can use individual variables:

```bash
# Database
DB_HOST=postgres.example.com
DB_PORT=5432
DB_NAME=vexa
DB_USER=postgres
DB_PASSWORD=your-password

# Redis
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
# Example: Use medium model for better transcription quality
docker run -d \
  -e WHISPER_MODEL_SIZE=medium \
  -e DATABASE_URL="..." \
  -e REDIS_URL="..." \
  -e ADMIN_API_TOKEN="..." \
  vexa-monolithic
```

**Note:** Models are downloaded on first use. Larger models require more RAM and CPU.

## Persistent Storage (Volumes)

For production deployments, mount volumes to persist data:

```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -p 8057:8057 \
  -v vexa-models:/root/.cache/huggingface \
  -v vexa-logs:/var/log/vexa-bots \
  -e DATABASE_URL="..." \
  -e REDIS_URL="..." \
  -e ADMIN_API_TOKEN="..." \
  vexa-monolithic
```

| Volume | Path | Description |
|--------|------|-------------|
| `vexa-models` | `/root/.cache/huggingface` | Downloaded Whisper models (avoid re-downloading) |
| `vexa-logs` | `/var/log/vexa-bots` | Bot process logs |

## Platform-Specific Deployment

### EasyPanel

1. Create a new **App** from Git repository or Docker image
2. Configure environment variables:
   - `DATABASE_URL` → Use EasyPanel PostgreSQL service URL
   - `REDIS_URL` → Use EasyPanel Redis service URL
   - `ADMIN_API_TOKEN` → Generate a secure token
3. Expose ports: `8056` (API), `8057` (Admin)
4. Optional: Add persistent volumes for models and logs

### Dokploy

1. Create a new **Application** → Docker deployment
2. Use `Dockerfile.monolithic` or pre-built image
3. Set environment variables in Dokploy's env section
4. Configure Redis and PostgreSQL services in Dokploy

### Railway / Render

1. Deploy from GitHub with `Dockerfile.monolithic`
2. Add PostgreSQL and Redis as managed services
3. Configure environment variables using service URLs
4. Set exposed port to `8056`

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
```

### Restart a Service

```bash
docker exec vexa supervisorctl restart vexa-core:whisperlive
docker exec vexa supervisorctl restart vexa-core:bot-manager
```

## Testing

### Create a User and Get API Key

```bash
# Create user (via Admin API)
curl -X POST "http://localhost:8057/users" \
  -H "X-Admin-Token: your-admin-token" \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "name": "Test User"}'

# Response includes API key:
# {"id": 1, "email": "test@example.com", "api_key": "vx_abc123..."}
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

## Comparison with Standard Deployment

| Feature | Standard (Docker Compose) | Monolithic |
|---------|---------------------------|------------|
| **Services** | Multiple containers | Single container |
| **Bot Spawning** | Docker containers | Node.js processes |
| **Docker Socket** | Required | Not required |
| **Traefik/Consul** | Included | Not needed |
| **GPU Support** | Yes | No (CPU only) |
| **Scaling** | Horizontal | Vertical |
| **Max Concurrent Bots** | Unlimited* | 3-5 recommended |
| **Complexity** | Higher | Lower |
| **Use Case** | Production, self-hosted | PaaS, simple deployments |

## Limitations

- **CPU Only:** GPU acceleration not supported in monolithic mode
- **Concurrent Bots:** Recommended max 3-5 (shared CPU/RAM)
- **Process Isolation:** Less isolated than container-per-bot
- **Model Size:** Larger models may be slow on limited resources

## Troubleshooting

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
| `Dockerfile.monolithic` | Main Dockerfile (in repo root) |
| `docker/monolithic/supervisord.conf` | Supervisor configuration |
| `docker/monolithic/entrypoint.sh` | Container initialization |
| `docker/monolithic/requirements-monolithic.txt` | Python dependencies |
| `services/bot-manager/app/orchestrators/process.py` | Process orchestrator |

## Changes from Open Source Project

The monolithic deployment adds the following without modifying core service code:

**New Files:**
- `Dockerfile.monolithic` - All-in-one container build
- `docker/monolithic/*` - Configuration files
- `services/bot-manager/app/orchestrators/process.py` - Process-based bot spawner

**Minimal Modifications:**
- `services/bot-manager/app/orchestrators/__init__.py` - Loads process orchestrator when `ORCHESTRATOR=process`
- `services/transcription-collector/config.py` - Added `REDIS_PASSWORD` support
- `services/transcription-collector/main.py` - Password parameter in Redis connection

All changes are **backwards compatible** and don't affect standard Docker Compose deployment.
