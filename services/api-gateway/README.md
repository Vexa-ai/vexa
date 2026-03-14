# API Gateway

## Why

Clients should not need to know the internal topology of Vexa services. The gateway provides a single entry point that routes requests to admin-api, bot-manager, transcription-collector, and MCP. It also handles concerns that span services: CORS, WebSocket fan-out for real-time meeting events, and public transcript share links. Without it, every client would need separate URLs and auth flows for each backend.

## What

A FastAPI reverse proxy that forwards authenticated requests to internal services. It owns no database -- every endpoint proxies to a downstream service via `httpx`, preserving headers, query params, and request bodies. It also maintains a Redis-backed WebSocket hub for real-time meeting status updates.

Key responsibilities:
- Route bot management, transcription, recording, voice agent, and admin requests to the correct backend
- Manage WebSocket connections that subscribe to meeting status via Redis Pub/Sub
- Generate and serve short-lived public transcript share links (stored in Redis)
- Forward MCP protocol requests to the MCP service

### Endpoints

**Bot Management** (proxied to bot-manager)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/bots` | Start a bot in a meeting |
| DELETE | `/bots/{platform}/{native_meeting_id}` | Stop a bot |
| PUT | `/bots/{platform}/{native_meeting_id}/config` | Update bot config (language, task) |
| GET | `/bots/status` | List running bots for the user |

**Voice Agent** (proxied to bot-manager)

| Method | Path | Description |
|--------|------|-------------|
| POST/DELETE | `/bots/{platform}/{id}/speak` | TTS speak / interrupt |
| POST/GET | `/bots/{platform}/{id}/chat` | Send / read chat messages |
| POST/DELETE | `/bots/{platform}/{id}/screen` | Show / stop screen share |
| PUT/DELETE | `/bots/{platform}/{id}/avatar` | Set / reset bot avatar |

**Recordings** (proxied to bot-manager)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/recordings` | List recordings |
| GET | `/recordings/{id}` | Get recording details |
| GET | `/recordings/{id}/media/{mid}/download` | Presigned download URL |
| DELETE | `/recordings/{id}` | Delete a recording |
| GET/PUT | `/recording-config` | Get/update recording config |

**Transcriptions** (proxied to transcription-collector)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/meetings` | List user's meetings |
| GET | `/transcripts/{platform}/{id}` | Get transcript |
| PATCH | `/meetings/{platform}/{id}` | Update meeting metadata |
| DELETE | `/meetings/{platform}/{id}` | Purge transcripts |
| POST | `/transcripts/{platform}/{id}/share` | Create public share link |
| GET | `/public/transcripts/{share_id}.txt` | Public transcript (no auth) |
| POST | `/meetings/{meeting_id}/transcribe` | Trigger deferred transcription |

**Admin** (proxied to admin-api)

| Method | Path | Description |
|--------|------|-------------|
| * | `/admin/{path}` | All admin/analytics endpoints |
| PUT | `/user/webhook` | Set user webhook URL |

**Other**

| Method | Path | Description |
|--------|------|-------------|
| * | `/mcp` | MCP protocol forwarding |
| WS | `/ws` | Real-time meeting status via WebSocket |

### Dependencies

- **admin-api** -- user/token management
- **bot-manager** -- bot lifecycle, recordings, voice agent
- **transcription-collector** -- meetings and transcripts
- **MCP service** -- Model Context Protocol
- **Redis** -- WebSocket Pub/Sub, transcript share link storage

## How

### Run

```bash
# Via docker-compose (from repo root)
docker compose up api-gateway

# Standalone
cd services/api-gateway
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Configure

| Variable | Description |
|----------|-------------|
| `ADMIN_API_URL` | Internal URL of admin-api (required) |
| `BOT_MANAGER_URL` | Internal URL of bot-manager (required) |
| `TRANSCRIPTION_COLLECTOR_URL` | Internal URL of transcription-collector (required) |
| `MCP_URL` | Internal URL of MCP service (required) |
| `REDIS_URL` | Redis URL for WebSocket Pub/Sub and share links |
| `PUBLIC_BASE_URL` | Public-facing base URL for share links (e.g., `https://api.vexa.ai`) |
| `TRANSCRIPT_SHARE_TTL_SECONDS` | Share link TTL (default: 900 = 15 min) |
| `TRANSCRIPT_SHARE_TTL_MAX_SECONDS` | Max allowed TTL (default: 86400 = 24h) |
| `CORS_ORIGINS` | Comma-separated allowed origins (default: `http://localhost:3000,http://localhost:3001`) |
| `LOG_LEVEL` | Logging level |

The service fails to start if any of `ADMIN_API_URL`, `BOT_MANAGER_URL`, `TRANSCRIPTION_COLLECTOR_URL`, or `MCP_URL` are missing.

### Test

```bash
# Health check
curl http://localhost:8000/

# Start a bot (requires user API key)
curl -X POST http://localhost:8000/bots \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"platform": "zoom", "native_meeting_id": "123456789"}'

# OpenAPI docs
open http://localhost:8000/docs
```

### Debug

- All proxied requests log method, URL, and response status to stdout
- Set `LOG_LEVEL=DEBUG` for header-level forwarding traces
- 503 errors mean a downstream service is unreachable
- WebSocket connections subscribe to Redis channels `bm:meeting:{id}:status`
