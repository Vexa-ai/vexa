# Meeting API

## Why

Every meeting platform (Google Meet, Teams, Zoom) has its own join flow, audio model, and lifecycle quirks. Something needs to own this complexity so the rest of the system doesn't have to care which platform a meeting is on. Meeting API is the domain boundary: it translates a single `POST /bots` request into platform-specific container orchestration, manages bot state through the full lifecycle (joining → active → completed), and exposes a uniform interface for voice agent controls, recordings, and status callbacks. Without it, every client would reimplement platform-specific bot management.

## What

Bot lifecycle management service. Handles meeting CRUD, voice agent controls (TTS, chat, screen sharing), recording management, and bot status callbacks from Runtime API.

**Port:** 8080 (default)

### Dependencies

- **Runtime API** — container lifecycle (create/stop)
- **PostgreSQL** — meeting state, recordings
- **Redis** — pub/sub, bot commands, chat messages

### API Endpoints

#### Meeting CRUD
- `POST /bots` — create meeting bot (supports `authenticated: true` for credential-based join)
- `GET /bots/status` — list running bots (`running_bots` array)
- `DELETE /bots/{platform}/{id}` — stop bot
- `PUT /bots/{platform}/{meeting_id}/config` — update config

#### Voice Agent
- `POST /bots/{platform}/{meeting_id}/speak` — TTS
- `POST /bots/{platform}/{meeting_id}/chat` — chat message
- `POST /bots/{platform}/{meeting_id}/screen` — screen content

#### Recordings
- `GET /bots/{platform}/{meeting_id}/recordings` — list recordings

#### Browser Sessions
- `POST /internal/browser-sessions/{token}/save` — trigger browser data save to S3
- `DELETE /internal/browser-sessions/{user_id}/storage` — delete stored browser data from S3

#### Internal Callbacks
- `POST /bots/internal/callback/exited` — bot exit
- `POST /bots/internal/callback/started` — bot startup
- `POST /bots/internal/callback/joining` — bot joining
- `POST /bots/internal/callback/awaiting_admission`
- `POST /bots/internal/callback/status_change`

#### Health
- `GET /health` → `{"status": "ok"}`

## How

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `REDIS_URL` | yes | — | Redis connection URL |
| `DATABASE_URL` | yes | — | PostgreSQL async connection string |
| `RUNTIME_API_URL` | no | `http://runtime-api:8000` | Runtime API base URL |
| `MEETING_API_URL` | no | `http://meeting-api:8080` | Self URL for bot callbacks |
| `BOT_IMAGE_NAME` | no | `vexaai/vexa-bot:latest` | Bot container image (use immutable `YYMMDD-HHMM` tags in dev) |
| `CORS_ORIGINS` | no | `http://localhost:3000,...` | Comma-separated CORS origins |
| `ADMIN_TOKEN` | yes | — | Secret for minting meeting JWTs |
| `TRANSCRIPTION_COLLECTOR_URL` | no | `http://transcription-collector:8000` | Transcription collector |

## Production Readiness

**Confidence: 52/100**

The service has solid domain logic, good test coverage of happy paths, and a well-structured async codebase. However, several critical bugs, security gaps, and operational blind spots prevent production deployment without remediation.

### Area Scores

| Area | Score | Evidence | Gap |
|------|-------|----------|-----|
| **Code quality** | 7/10 | Clean async FastAPI, proper separation of concerns, good use of Pydantic validation, well-structured modules | Deprecated `datetime.utcnow()` throughout, deprecated `@app.on_event` lifecycle hooks, unreachable code in `auth.py:46`, `recreate_db()` DROP SCHEMA CASCADE exists in production code (`database.py`) |
| **Test coverage** | 6/10 | 161 tests all passing, covers auth, CRUD, callbacks, voice agent, webhooks, recordings, collector (40+ tests) | All 161 tests are fully mocked (MockDB, MockRedis, MockResult) — zero tests against real infrastructure. Integration tests (`test_integration_live.py`) require manual `docker compose up` and skip if unavailable. No CI runs integration tests |
| **Auth rewrite** | 7/10 | Dual-mode auth works: gateway headers (`X-User-ID`) for production, API keys for standalone. `UserProxy` pattern is clean | Unreachable exception at `auth.py:46`. No rate limiting. No token expiry validation on API keys. Auth middleware returns 403 for both missing and invalid keys (should be 401 for missing) |
| **Model split** | 5/10 | Own `declarative_base()`, own models (`Meeting`, `Transcription`, `MeetingSession`, `Recording`, `MediaFile`, `CalendarEvent`) | `CalendarEvent` model defined but unused by any endpoint. `collector/auth.py` and `collector/endpoints.py` still import `admin_models.models.APIToken`, `admin_models.models.User`, and `admin_models.token_scope` — tight coupling to old shared library breaks standalone operation. `schemas.py` still contains admin API schemas (`UserBase`, `UserCreate`, `UserResponse`) that are vestiges from old shared models |
| **TC fold** | 7/10 | Redis Stream consumer with consumer groups, stale message claiming, UPSERT by `segment_id`, background task lifecycle management in `main.py` | Issuer fixed: accepts only `iss: "meeting-api"` (legacy `bot-manager` issuer removed 2026-03-30 — all legacy tokens expired). Connection retry is basic (sleep + retry loop, no backoff) |
| **Webhook delivery** | 9/10 | Exponential backoff with jitter, HMAC-SHA256 signing, Redis-backed retry queue, SSRF protection (blocks private IPs, link-local, cloud metadata, internal Docker hostnames), DNS resolution validation, 24h max retry window, backoff schedule `[60, 300, 1800, 7200]s` | No dead-letter queue or alerting after final failure. Webhook secret is per-meeting (stored in `meeting.data` JSONB) but no rotation mechanism |
| **Standalone docker-compose** | 6/10 | Full stack: meeting-api, runtime-api, redis, postgres with healthchecks. Makefile with `up`, `down`, `test-unit`, `test-integration` targets | No MinIO/S3 service despite storage abstraction requiring it. No Alembic migration step in startup — relies on `create_all()`. No volume mounts for postgres data persistence. No `.env.example` |
| **Frozen contracts** | 7/10 | Pydantic schemas enforce response shapes. `MeetingStatus` enum with validated state machine transitions. Platform enum. Integration tests verify endpoint shapes | No OpenAPI schema export or contract tests. No versioning strategy. `BotStatusResponse.running_bots` shape is tested but not formally frozen |
| **Recordings/storage** | 5/10 | Abstract `StorageClient` with MinIO and Local backends. Path traversal protection in `LocalStorageClient`. Dual-mode recording lookup (JSONB vs normalized model) | **N+1 query**: `_find_meeting_data_recording` scans ALL user meetings to find a recording. No pagination on recording queries. MinIO not in docker-compose. No storage health check. Dual-mode lookup adds complexity without clear migration path |
| **Docker** | 7/10 | Python 3.11-slim, multi-stage-ready, healthcheck configured, proper `--no-cache-dir` pip installs | Still copies and installs `admin-models` shared lib (breaks if lib changes). No multi-stage build (gcc stays in final image). No non-root user. No `.dockerignore` found. Image not pinned to specific Python patch version |
| **Performance** | 4/10 | Async throughout, SQLAlchemy async with asyncpg, connection pool configured (`pool_size=10`, `max_overflow=20`) | **No httpx connection pooling**: creates new `AsyncClient` per Runtime API call in `meetings.py`. No request timeout configuration on outbound calls. `statement_cache_size=0` disables asyncpg prepared statement cache. No metrics/observability (no Prometheus, no structured logging). `REDIS_URL` required at import time (raises `ValueError` on import) |

### Known Limitations

1. **MeetingToken issuer** (fixed 2026-03-30): `verify_meeting_token()` in `collector/processors.py` accepts only `iss: "meeting-api"`. Legacy `bot-manager` issuer removed — all legacy tokens expired (2h TTL, service deleted 2026-03-26).

2. **SecurityHeadersMiddleware not mounted**: `security_headers.py` defines the middleware and `main.py` imports it, but `app.add_middleware(SecurityHeadersMiddleware)` is never called. All security headers (X-Frame-Options, CSP, X-Content-Type-Options) are silently missing.

3. **admin_models coupling**: `collector/auth.py` and `collector/endpoints.py` import from `admin_models.models` and `admin_models.token_scope`. The Dockerfile copies and installs this shared library. This blocks true standalone operation and creates a fragile cross-package dependency.

4. **`recreate_db()` in production code**: `database.py` contains a function that runs `DROP SCHEMA public CASCADE`. No guard prevents accidental invocation. Should be removed or moved to a dev-only script.

5. **No httpx connection pooling**: Every Runtime API call in `meetings.py` creates a new `httpx.AsyncClient`, opening and closing TCP connections per request. Under load this will exhaust file descriptors and add latency.

6. **N+1 recording query**: `_find_meeting_data_recording()` in `recordings.py` loads ALL meetings for a user to search for a recording by ID, instead of querying directly.

7. **Vestigial schemas**: `schemas.py` contains `UserBase`, `UserCreate`, `UserResponse`, `UserUpdate` and other admin-API schemas that are unused by meeting-api endpoints.

8. **No observability**: No Prometheus metrics, no structured logging, no request tracing. The only health signal is `GET /health → {"status": "ok"}` which doesn't check downstream dependencies.

9. **TRANSCRIPTION_SERVICE_URL missing from profiles.yaml (bug #23, FIXED)** — `supervisord.conf` in lite mode was not passing `REDIS_URL` and `TRANSCRIPTION_SERVICE_URL` to meeting-api process. Bots launched by meeting-api couldn't reach the transcription service. Fixed by adding environment variables to supervisord meeting-api config.

10. **GET /chat fails for completed meetings (bug #29)** — `voice_agent.py` uses `_find_active_meeting()` for chat read, which filters by active status. After meeting completion, chat data in Redis becomes inaccessible via API. Fix: use a read-path function that doesn't require active status.

### Validation Plan (to reach 90+)

1. **Fix MeetingToken issuer** — align `iss` claim between `mint_meeting_token` and `verify_meeting_token`. Add a unit test that mints and verifies in sequence. *Moves confidence: +8*

2. **Mount SecurityHeadersMiddleware** — add `app.add_middleware(SecurityHeadersMiddleware)` in `main.py`. Add test verifying headers present. *Moves confidence: +3*

3. **Remove admin_models dependency** — inline the 2-3 needed types (APIToken, User, check_token_scope) into meeting-api's own models or remove the collector auth paths that reference them. Update Dockerfile to stop copying admin-models. *Moves confidence: +5*

4. **Create shared httpx client** — instantiate one `httpx.AsyncClient` at app startup (in lifespan), pass it via `app.state` or dependency injection, close on shutdown. *Moves confidence: +4*

5. **Fix N+1 recording query** — replace `_find_meeting_data_recording` scan with a direct query filtered by recording ID. *Moves confidence: +3*

6. **Remove `recreate_db()`** — delete from `database.py` or move to a CLI-only dev script. *Moves confidence: +2*

7. **Clean up schemas.py** — remove unused admin schemas (UserBase, UserCreate, UserResponse, UserUpdate). Remove unused CalendarEvent model. *Moves confidence: +2*

8. **Add real integration tests to CI** — run `docker compose up` in CI, execute `test_integration_live.py`, tear down. Add a postgres migration test. *Moves confidence: +6*

9. **Add observability** — structured JSON logging, Prometheus metrics endpoint (`/metrics`), request duration histograms, downstream health in `/health` (redis ping, db ping). *Moves confidence: +5*

10. **Docker hardening** — multi-stage build (drop gcc), non-root user, pin Python patch version, add `.dockerignore`. *Moves confidence: +3*

11. **Add MinIO to docker-compose** — recordings/storage won't work standalone without it. Add healthcheck. *Moves confidence: +2*

12. **Fix deprecated APIs** — replace `datetime.utcnow()` with `datetime.now(UTC)`, replace `@app.on_event` with lifespan context manager. *Moves confidence: +2*

**Projected score after all items: ~97/100**

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | `GET /health` returns `{"status": "ok"}` | 10 | ceiling | untested | — | — | — |
| 2 | `POST /bots` creates meeting bot and triggers container via Runtime API | 25 | ceiling | untested | — | — | — |
| 3 | `GET /bots/status` returns running bots for user | 10 | — | untested | — | — | — |
| 4 | Internal callbacks (`/bots/internal/callback/*`) update meeting state | 15 | ceiling | untested | — | — | — |
| 5 | PostgreSQL reachable at `DATABASE_URL` and Redis at `REDIS_URL` | 20 | ceiling | untested | — | — | — |
| 6 | Runtime API reachable at `RUNTIME_API_URL` for container lifecycle | 20 | ceiling | untested | — | — | — |

Confidence: 0 (untested)

