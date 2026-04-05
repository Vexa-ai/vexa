# API Gateway

## Why

Clients should not need to know the internal topology of Vexa services. The gateway provides a single entry point that routes requests to admin-api, meeting-api, transcription-collector, and MCP. It also handles concerns that span services: CORS, WebSocket fan-out for real-time meeting events, and public transcript share links. Without it, every client would need separate URLs and auth flows for each backend.

## What

A FastAPI reverse proxy that forwards authenticated requests to internal services. It owns no database -- every endpoint proxies to a downstream service via `httpx`, preserving headers, query params, and request bodies. It also maintains a Redis-backed WebSocket hub for real-time meeting status updates.

### Documentation
- [Quickstart](../../docs/quickstart.mdx)
- [Getting Started](../../docs/getting-started.mdx)
- [Errors and Retries](../../docs/errors-and-retries.mdx)
- [WebSocket API](../../docs/websocket.mdx)
- [Token Scoping](../../docs/token-scoping.mdx)
- [Security](../../docs/security.mdx)

Key responsibilities:
- Route bot management, transcription, recording, voice agent, and admin requests to the correct backend
- Manage WebSocket connections that subscribe to meeting status via Redis Pub/Sub
- Generate and serve short-lived public transcript share links (stored in Redis)
- Forward MCP protocol requests to the MCP service

### Endpoints

**Bot Management** (proxied to meeting-api)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/bots` | Start a bot in a meeting |
| DELETE | `/bots/{platform}/{native_meeting_id}` | Stop a bot |
| PUT | `/bots/{platform}/{native_meeting_id}/config` | Update bot config (language, task) |
| GET | `/bots/status` | List running bots for the user |

**Voice Agent** (proxied to meeting-api)

| Method | Path | Description |
|--------|------|-------------|
| POST/DELETE | `/bots/{platform}/{id}/speak` | TTS speak / interrupt |
| POST/GET | `/bots/{platform}/{id}/chat` | Send / read chat messages |
| POST/DELETE | `/bots/{platform}/{id}/screen` | Show / stop screen share |
| PUT/DELETE | `/bots/{platform}/{id}/avatar` | Set / reset bot avatar |

**Recordings** (proxied to meeting-api)

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

**Bot Browser View** (proxied to container, token/meeting-ID authenticated)

The `{token}` can be a meeting ID (integer) or a session_token (random string). Both resolve via Redis `browser_session:{token}` to the container name.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/b/{token}` | Browser dashboard page (embedded VNC + controls) |
| GET/WS | `/b/{token}/vnc/{path}` | Proxy to noVNC web client (HTTP assets + websockify WebSocket) — works for any bot |
| GET/WS | `/b/{token}/cdp/{path}` | Proxy to Chrome DevTools Protocol endpoint (browser sessions only) |
| POST | `/b/{token}/save` | Trigger storage save (browser sessions only) |
| DELETE | `/b/{token}/storage` | Delete stored browser data from S3 (clean start) |

**User Settings** (proxied to admin-api)

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/user/workspace-git` | Set git workspace config (repo, token, branch) for browser sessions |
| DELETE | `/user/workspace-git` | Remove git workspace config |

**Other**

| Method | Path | Description |
|--------|------|-------------|
| * | `/mcp` | MCP protocol forwarding |
| WS | `/ws` | Real-time meeting status via WebSocket |

### Dependencies

- **admin-api** -- user/token management
- **meeting-api** -- bot lifecycle, recordings, voice agent
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
| `MEETING_API_URL` | Internal URL of meeting-api (required) |
| `TRANSCRIPTION_COLLECTOR_URL` | Internal URL of transcription-collector (required) |
| `MCP_URL` | Internal URL of MCP service (required) |
| `REDIS_URL` | Redis URL for WebSocket Pub/Sub and share links |
| `PUBLIC_BASE_URL` | Public-facing base URL for share links (e.g., `https://api.vexa.ai`) |
| `TRANSCRIPT_SHARE_TTL_SECONDS` | Share link TTL (default: 900 = 15 min) |
| `TRANSCRIPT_SHARE_TTL_MAX_SECONDS` | Max allowed TTL (default: 86400 = 24h) |
| `CORS_ORIGINS` | Comma-separated allowed origins for CORS (default: `http://localhost:3000,http://localhost:3001`). Controls `Access-Control-Allow-Origin` for all endpoints. |
| `LOG_LEVEL` | Logging level |

The service fails to start if any of `ADMIN_API_URL`, `MEETING_API_URL`, `TRANSCRIPTION_COLLECTOR_URL`, or `MCP_URL` are missing.

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
- WebSocket connections subscribe to Redis channels `meeting:{id}:status`

## Production Readiness

**Confidence: 68/100**

| Area | Score | Evidence | Gap |
|------|-------|----------|-----|
| Proxy routing | 9/10 | ~50 endpoints correctly forwarded; test_routes.py confirms all exist | Voice agent endpoints (speak, chat, screen) untested in integration |
| Header injection & cache | 8/10 | Strips spoofed X-User-* headers before injection; 60s Redis TTL; graceful fallback when Redis/admin-api down | No early cache invalidation (revoked tokens valid up to 60s); admin-api response structure not validated before injection |
| Token validation | 8/10 | test_header_injection.py covers valid/invalid/cached/spoofed cases. Cache key uses sha256 hash (no prefix collision). Scopes come from DB via admin-api. | Cannot flush cache on token revocation (60s TTL). |
| WebSocket hub | 6/10 | Redis Pub/Sub fan-out for meeting status; subscribe authorization delegated to TC | No unit tests for actual message flow; no rate limit on subscribe (DoS vector); concurrent subscribe edge case with duplicate meetings |
| Remote browser proxy | 7/10 | VNC + CDP WebSocket proxying with token auth; binary + JSON-RPC protocols handled | No unit tests; `print()` debug statement on line 1373 instead of logger; token in URL leaks to browser history |
| Public share links | 8/10 | `secrets.token_urlsafe(16)` (~2^100 bits); configurable TTL with max cap (24h); proper cache headers | Share link token in URL can leak via logs/browser history (mitigated by TTL) |
| CORS | 4/10 | Configurable via env var; defaults to localhost:3000,3001 | **Production risk**: if `CORS_ORIGINS` not set, all browser requests silently blocked. No startup warning |
| Error handling | 8/10 | 503 for unreachable backends; 504 for timeouts; graceful degradation throughout | Error messages from downstream can leak service topology |
| MCP forwarding | 7/10 | GET/POST/SSE proxying; header filtering; session ID workaround | 400→200 workaround for missing session ID may mask real errors |
| Tests | 6/10 | 5 test files covering routes, headers, helpers, bot routes, recording routes | No WebSocket tests; no remote browser tests; no MCP protocol tests; no rate limiting tests |
| Docker | 8/10 | Installs meeting-api package for schemas; non-root user (appuser) | No HEALTHCHECK in Dockerfile |
| Security | 7/10 | Identity headers stripped before injection; constant-time comparison; SSRF validation on webhooks | No rate limiting on WebSocket subscribe; CDP proxy trusts Redis-sourced hostnames without validation |

### Known Limitations

1. **CORS defaults block production** — `CORS_ORIGINS` defaults to `localhost:3000,3001`. Any production deployment that forgets this env var will have all browser requests silently rejected. No startup warning.
2. **Token revocation has 60s delay** — cached tokens remain valid for up to 60 seconds after revocation. No cache invalidation endpoint exists.
3. **Rate limiting implemented** — Redis sliding window, 3 tiers: API (120/min), admin (30/min), WS (20/min). Configurable via env vars. Returns 429 with Retry-After header. ~~Previously no rate limiting.~~ Fixed 2026-03-29. WebSocket subscribe rate limiting still separate (not yet done).
4. **Debug print statement** — line 1373 uses `print()` instead of `logger.debug()` for CDP proxy URL rewriting.
5. **WebSocket integration tests exist (2026-04-05)** — 8/8 WS checks pass: connect with valid key, reject without key (4401), ping/pong, subscribe to meeting, unsubscribe, invalid JSON recovery, segment validation (no duplicates, all have text+speaker), unknown action error. Tested on both GMeet and Teams meetings. Live streaming during active meeting not yet tested (protocol-only validation).
6. **Admin-api response not validated** — if admin-api returns malformed user_data (e.g., `user_id: null`), it gets injected as `X-User-ID: None` and cached for 60s.
7. **CDP proxy hardcodes port 9223 (bug #21)** — `main.py` CDP proxy targets port 9223 on bot containers. In compose mode, `socat` in the bot entrypoint bridges 9223→9222. In lite mode (process backend), Chrome listens on 9222 directly — no socat. CDP proxy fails with connection refused in lite mode. Fix: make the port configurable or auto-detect.

### Validation Plan (to reach 90+)

- [x] **P0**: Add gateway-level rate limiting (configurable, returns 429) — done 2026-03-29, 3 tiers via RATE_LIMIT_RPM env vars
- [ ] **P0**: Require `CORS_ORIGINS` env var at startup for non-dev environments (fail fast)
- [ ] Replace `print()` on line 1373 with `logger.debug()`
- [ ] Validate admin-api response structure before header injection (user_id is int, scopes is list)
- [ ] Add rate limiter to WebSocket subscribe (e.g., 10/min per connection)
- [ ] Add cache invalidation endpoint (admin-only) to flush token cache on revocation
- [ ] Add WebSocket integration test for subscribe → publish → receive round-trip
- [ ] Add HEALTHCHECK to Dockerfile
- [ ] Validate container hostname pattern in CDP proxy (`^vexa-bot-[a-z0-9]+$`)
- [ ] Truncate downstream error messages to prevent topology leakage
