# Bot Manager

## Why

Each meeting bot runs as an isolated container (or process). Something needs to create those containers, track their lifecycle, handle callbacks when bots join/exit/fail, manage recordings, and enforce per-user concurrency limits. The bot-manager is that orchestrator -- it is the only service with Docker socket access and the authority to start/stop bot instances. Without it, there is no way to launch bots or know their state.

## What

A FastAPI service that orchestrates the full bot lifecycle: create meeting records, launch bot containers, process status callbacks from running bots, manage recordings and media files, and expose voice agent controls (speak, chat, screen share, avatar). It authenticates users via API tokens (`X-API-Key`), writes to PostgreSQL, coordinates through Redis, and fires webhooks on status changes.

Architecture position: sits between the api-gateway (which proxies client requests) and the bot containers (which call back to bot-manager's internal endpoints).

### Endpoints

**Bot Lifecycle**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/bots` | Create meeting + launch bot container |
| DELETE | `/bots/{platform}/{native_meeting_id}` | Stop a bot and its container |
| PUT | `/bots/{platform}/{native_meeting_id}/config` | Update language/task for active bot via Redis |
| GET | `/bots/status` | List running bots for the authenticated user |

**Internal Callbacks** (called by bot containers, not clients)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/bots/internal/callback/started` | Bot container started successfully |
| POST | `/bots/internal/callback/joining` | Bot is joining the meeting |
| POST | `/bots/internal/callback/awaiting_admission` | Bot waiting in lobby |
| POST | `/bots/internal/callback/status_change` | Unified status change handler |
| POST | `/bots/internal/callback/exited` | Bot exited (success or failure) |
| POST | `/internal/recordings/upload` | Upload recording media |

**Recordings**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/recordings` | List recordings for the user |
| GET | `/recordings/{id}` | Get recording with media files |
| GET | `/recordings/{id}/media/{mid}/download` | Presigned download URL |
| GET | `/recordings/{id}/media/{mid}/raw` | Stream media bytes directly |
| DELETE | `/recordings/{id}` | Delete recording + media from storage |
| GET/PUT | `/recording-config` | Get/update user recording preferences |

**Voice Agent**

| Method | Path | Description |
|--------|------|-------------|
| POST/DELETE | `/bots/{platform}/{id}/speak` | TTS speak / interrupt |
| POST/GET | `/bots/{platform}/{id}/chat` | Send / read chat messages |
| POST/DELETE | `/bots/{platform}/{id}/screen` | Show / stop screen content |
| PUT/DELETE | `/bots/{platform}/{id}/avatar` | Set / reset bot avatar |
| POST | `/bots/{meeting_id}/agent/chat` | Send agent chat message |
| DELETE | `/bots/{meeting_id}/agent/chat` | Clear agent chat history |
| POST | `/bots/{meeting_id}/agent/chat/reset` | Reset agent chat session |

**Deferred Transcription**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/meetings/{meeting_id}/transcribe` | Transcribe a completed meeting from its recording |

### Dependencies

- **PostgreSQL** -- meetings, meeting_sessions, recordings, media_files, users, api_tokens
- **Redis** -- bot locking, bot-to-container mapping, status pub/sub, config commands
- **Docker socket** -- container creation and management (or process orchestrator in lite mode)
- **shared_models** -- ORM models, schemas, storage client, webhook delivery
- **TTS service** -- text-to-speech for voice agent speak commands
- **Object storage** -- MinIO, S3, or local filesystem for recording media

## How

### Run

```bash
# Via docker-compose (from repo root)
docker compose up bot-manager

# Standalone (requires Docker socket access)
cd services/bot-manager
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Configure

| Variable | Description |
|----------|-------------|
| `REDIS_URL` | Redis connection URL (required) |
| `BOT_IMAGE_NAME` | Docker image for bot containers (default: `vexa-bot:latest`) |
| `DOCKER_NETWORK` | Docker network to attach bot containers to |
| `TTS_SERVICE_URL` | URL of the TTS service |
| `ADMIN_TOKEN` | Shared secret for minting MeetingToken JWTs |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL connection |
| `STORAGE_BACKEND` | `minio`, `s3`, or `local` |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` | MinIO config |
| `RECORDING_ENABLED` | Enable recording by default (default: `false`) |
| `CAPTURE_MODES` | Recording capture modes (default: `audio`) |
| `BOT_STOP_DELAY_SECONDS` | Delay before force-killing container after stop (default: `90`) |
| `ORCHESTRATOR` | Container orchestrator: `docker`, `kubernetes`, or `process` |
| `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` | Zoom OAuth credentials |
| `LOG_LEVEL` | Logging level (default: `INFO`) |

### Test

```bash
# Health check
curl http://localhost:8080/

# Start a bot (requires user API key)
curl -X POST http://localhost:8080/bots \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"platform": "zoom", "native_meeting_id": "123456789"}'

# Check running bots
curl http://localhost:8080/bots/status -H "X-API-Key: $API_KEY"
```

### Debug

- Logs to stdout: `%(asctime)s - bot_manager - %(levelname)s - %(message)s`
- Set `LOG_LEVEL=DEBUG` for container lifecycle traces
- Bot containers call back to `/bots/internal/callback/*` -- check those logs for join/exit issues
- Redis keys: `bot_lock:*` (concurrency), `bot_map:*` (container mapping), `bot_status:*` (state)
- Meeting status transitions are validated; invalid transitions log warnings and return 400
