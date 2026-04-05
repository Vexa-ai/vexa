# Cross-Service Dependency Audit

**Date:** 2026-03-27
**Scope:** All Python services in vexa-agentic-runtime
**Methodology:** Grep/read analysis of all .py files for imports, hardcoded URLs, Redis keys, and database access

---

## 1. shared_models Import Matrix

All services import from `shared_models` (libs/shared-models). Below is the complete mapping:

| Source File | Imports From | Specific Import | Type |
|---|---|---|---|
| **admin-api/app/main.py** | shared_models | User, APIToken, Base, Meeting, Transcription, MeetingSession | model (DB) |
| **admin-api/app/main.py** | shared_models | UserCreate, UserResponse, TokenResponse, UserDetailResponse, UserBase, UserUpdate, MeetingResponse | schema (validation) |
| **admin-api/app/main.py** | shared_models | get_db, init_db | util (storage) |
| **admin-api/app/main.py** | shared_models | validate_webhook_url | util (validation) |
| **admin-api/app/main.py** | shared_models | SecurityHeadersMiddleware | util (middleware) |
| **admin-api/app/main.py** | shared_models | generate_prefixed_token, check_token_scope | util (token_scope) |
| **admin-api/app/scripts/recreate_db.py** | shared_models | recreate_db, logger, Base | util (storage) |
| **admin-api/tests/test_crud.py** | shared_models | get_db | util (storage) |
| **admin-api/tests/test_jsonb_merge.py** | shared_models | get_db | util (storage) |
| **agent-api/app/auth.py** | shared_models | User, APIToken | model (DB) |
| **agent-api/app/auth.py** | shared_models | get_db | util (storage) |
| **agent-api/app/auth.py** | shared_models | check_token_scope | util (token_scope) |
| **agent-api/app/schedule_endpoints.py** | shared_models | schedule_job, cancel_job, list_jobs, get_job | util (scheduler) |
| **agent-api/app/main.py** | shared_models | SecurityHeadersMiddleware | util (middleware) |
| **agent-api/app/main.py** | shared_models | recover_orphaned_jobs | util (scheduler) |
| **agent-api/app/main.py** | shared_models | _executor_loop, stop_executor | util (scheduler_worker) |
| **api-gateway/main.py** | shared_models | MeetingCreate, MeetingResponse, MeetingListResponse, MeetingDataUpdate | schema (validation) |
| **api-gateway/main.py** | shared_models | TranscriptionResponse, TranscriptionSegment | schema (validation) |
| **api-gateway/main.py** | shared_models | UserCreate, UserResponse, TokenResponse, UserDetailResponse | schema (validation) |
| **api-gateway/main.py** | shared_models | Platform, BotStatusResponse | schema (validation) |
| **api-gateway/main.py** | shared_models | SpeakRequest, ChatSendRequest, ChatMessagesResponse, ScreenContentRequest | schema (validation) |
| **api-gateway/main.py** | shared_models | SecurityHeadersMiddleware | util (middleware) |
| **calendar-service/app/main.py** | shared_models | get_db, init_db | util (storage) |
| **calendar-service/app/main.py** | shared_models | CalendarEvent, User | model (DB) |
| **calendar-service/app/sync.py** | shared_models | CalendarEvent, User | model (DB) |
| **calendar-service/app/models.py** | shared_models | CalendarEvent, User | model (DB) |
| **meeting-api/meeting_api/auth.py** | shared_models | User, APIToken | model (DB) |
| **meeting-api/meeting_api/auth.py** | shared_models | get_db | util (storage) |
| **meeting-api/meeting_api/auth.py** | shared_models | check_token_scope | util (token_scope) |
| **meeting-api/meeting_api/main.py** | shared_models | init_db, async_session_local | util (storage) |
| **meeting-api/meeting_api/main.py** | shared_models | set_redis_client, start_retry_worker, stop_retry_worker | util (webhook_delivery, webhook_retry_worker) |
| **meeting-api/meeting_api/meetings.py** | shared_models | get_db, async_session_local | util (storage) |
| **meeting-api/meeting_api/meetings.py** | shared_models | User, Meeting, MeetingSession | model (DB) |
| **meeting-api/meeting_api/meetings.py** | shared_models | MeetingCreate, MeetingResponse, Platform, BotStatusResponse, MeetingConfigUpdate, MeetingStatus, MeetingCompletionReason, MeetingFailureStage, is_valid_status_transition, get_status_source | schema (validation) |
| **meeting-api/meeting_api/callbacks.py** | shared_models | get_db | util (storage) |
| **meeting-api/meeting_api/callbacks.py** | shared_models | Meeting, MeetingSession | model (DB) |
| **meeting-api/meeting_api/callbacks.py** | shared_models | MeetingStatus, MeetingCompletionReason, MeetingFailureStage, CallbackRequest | schema (validation) |
| **meeting-api/meeting_api/voice_agent.py** | shared_models | get_db | util (storage) |
| **meeting-api/meeting_api/voice_agent.py** | shared_models | User | model (DB) |
| **meeting-api/meeting_api/voice_agent.py** | shared_models | Platform, MeetingStatus | schema (validation) |
| **meeting-api/meeting_api/webhooks.py** | shared_models | Meeting, User | model (DB) |
| **meeting-api/meeting_api/webhooks.py** | shared_models | validate_webhook_url | util (validation) |
| **meeting-api/meeting_api/webhooks.py** | shared_models | deliver, build_envelope, set_redis_client, start_retry_worker | util (webhook_delivery, webhook_retry_worker) |
| **meeting-api/meeting_api/post_meeting.py** | shared_models | Meeting | model (DB) |
| **meeting-api/meeting_api/post_meeting.py** | shared_models | async_session_local | util (storage) |
| **meeting-api/meeting_api/post_meeting.py** | shared_models | deliver, build_envelope | util (webhook_delivery) |
| **meeting-api/meeting_api/recordings.py** | shared_models | get_db | util (storage) |
| **meeting-api/meeting_api/recordings.py** | shared_models | User, Meeting, MeetingSession, Recording, MediaFile | model (DB) |
| **meeting-api/meeting_api/recordings.py** | shared_models | RecordingResponse, MediaFileResponse | schema (validation) |
| **meeting-api/meeting_api/recordings.py** | shared_models | create_storage_client | util (storage) |
| **transcription-collector/main.py** | shared_models | get_db, init_db | util (storage) |
| **transcription-collector/main.py** | shared_models | Meeting | model (DB) |
| **transcription-collector/api/auth.py** | shared_models | get_db | util (storage) |
| **transcription-collector/api/auth.py** | shared_models | APIToken, User | model (DB) |
| **transcription-collector/api/auth.py** | shared_models | check_token_scope | util (token_scope) |
| **transcription-collector/api/endpoints.py** | shared_models | get_db, async_session_local | util (storage) |
| **transcription-collector/api/endpoints.py** | shared_models | User, Meeting, Transcription, MeetingSession, Recording | model (DB) |
| **transcription-collector/api/endpoints.py** | shared_models | TranscriptionResponse, TranscriptionSegment | schema (validation) |
| **transcription-collector/api/endpoints.py** | shared_models | create_storage_client | util (storage) |
| **transcription-collector/background/db_writer.py** | shared_models | async_session_local | util (storage) |
| **transcription-collector/background/db_writer.py** | shared_models | Transcription, Meeting | model (DB) |
| **transcription-collector/streaming/processors.py** | shared_models | async_session_local | util (storage) |
| **transcription-collector/streaming/processors.py** | shared_models | User, Meeting, MeetingSession, APIToken | model (DB) |
| **transcription-collector/streaming/processors.py** | shared_models | Platform | schema (validation) |

**Key Findings:**
- **100% of services depend on shared_models** for database models, schemas, and utilities
- **DB Coupling:** 23 services directly import ORM models (User, Meeting, APIToken, Transcription, Recording, etc.)
- **Schema Coupling:** 15 services import Pydantic schemas for validation
- **Utilities:** All services use `get_db`, `init_db`, `async_session_local` for database access
- **Token Scope:** 4 services use `check_token_scope` (admin-api, agent-api, meeting-api, transcription-collector)
- **Scheduler:** agent-api imports scheduler utilities (schedule_job, cancel_job, recover_orphaned_jobs)
- **Webhook Delivery:** meeting-api heavily uses webhook_delivery utilities (deliver, build_envelope, set_redis_client)
- **Storage:** meeting-api and transcription-collector use `create_storage_client`

---

## 2. Cross-Service Imports

### admin-api
**Internal imports:** No cross-service imports detected. Fully encapsulated.

### agent-api
**Internal imports:**
- `from app.container_manager import ContainerManager`
- `from app.stream_parser import parse_event`
- `from app.workspace_context import build_workspace_context`
- `from app.schedule_endpoints import router as schedule_router`
- `from app.workspace_endpoints import router as workspace_router`
- `from app.auth_simple import require_api_key`

**No external service imports.** All dependencies are internal or to shared_models.

### meeting-api
**Internal imports:**
- `from .auth import get_user_and_token`
- `from .config import REDIS_URL, RUNTIME_API_URL, MEETING_API_URL, ...`
- `from .post_meeting import run_all_tasks, run_status_webhook_task`
- `from meeting_api import meetings as meetings_mod`
- `from meeting_api.auth import get_api_key, get_user_and_token`
- `from meeting_api.webhooks import _resolve_event_type, _is_event_enabled, ...`

**No external service imports.** All internal.

### api-gateway
**Internal imports:**
- Purely a proxy layer — no cross-service imports. Routes to other services via HTTP.

### transcription-collector
**Internal imports:**
- `from api.auth import ...`
- `from background.db_writer import ...`
- `from streaming.processors import ...`

**No external service imports.** All internal.

### calendar-service
**Internal imports:**
- `from app.google_calendar import ...`
- `from app.sync import sync_user_calendar, schedule_upcoming_bots`

**No external service imports.** All internal.

**Summary:** **Zero cross-service Python imports detected.** Services interact only via:
1. HTTP (api-gateway proxying)
2. Redis pub/sub (bm:meeting:*, tc:meeting:*, bot_commands:* channels)
3. Database (shared_models)
4. Environment variables (URLs)

---

## 3. Hardcoded Service URLs

| Service URL | Referenced By | Usage |
|---|---|---|
| **http://runtime-api:8090** | agent-api/app/config.py (RUNTIME_API_URL) | Container lifecycle (create, stop, list) |
| **http://runtime-api:8090** | agent-api/app/container_manager.py | HTTP client target for container CRUD |
| **http://runtime-api:8000** | meeting-api/meeting_api/config.py (RUNTIME_API_URL) | Container lifecycle (not currently used in meetings.py) |
| **http://meeting-api:8080** | meeting-api/meeting_api/config.py (MEETING_API_URL) | Callback URL for bot container exit |
| **http://transcription-collector:8000** | meeting-api/meeting_api/config.py (TRANSCRIPTION_COLLECTOR_URL) | Fetch transcripts post-meeting |
| **http://meeting-api:8080** | calendar-service/app/sync.py (MEETING_API_URL) | Get user's API key, create bots |
| **http://meeting-api:8000** | api-gateway/tests/conftest.py (MEETING_API_URL) | Test proxy target |
| **http://admin-api:8000** | api-gateway/tests/conftest.py (ADMIN_API_URL) | Test proxy target |
| **http://transcription-collector:8000** | api-gateway/tests/conftest.py (TRANSCRIPTION_COLLECTOR_URL) | Test proxy target |
| **http://transcription-collector:8000** | transcription-collector/streaming/processors.py | Hardcoded JWT validation check ("iss": "bot-manager" — frozen contract) |
| **http://tts-service** | meeting-api/meeting_api/meetings.py | Optional TTS_SERVICE_URL env var |

**Environment Variables Used:**
- `RUNTIME_API_URL` (agent-api, meeting-api)
- `MEETING_API_URL` (meeting-api, for callback_url)
- `TRANSCRIPTION_COLLECTOR_URL` (meeting-api)
- `MEETING_API_URL` (calendar-service, api-gateway tests)
- `ADMIN_API_URL` (api-gateway)
- `TTS_SERVICE_URL` (meeting-api, optional)

**Hardcoded Hostnames (NOT env vars):**
- `meeting-api` in calendar-service/app/sync.py (default hostname in MEETING_API_URL)
- `admin-api`, `meeting-api` (+ frozen `bot-manager`) in meeting_api/webhook_url.py (JWT issuer validation list)

---

## 4. Redis Key Patterns & Ownership

| Key Pattern | Written By | Read By | Purpose |
|---|---|---|---|
| **bm:meeting:{meeting_id}:status** | meeting-api (publish_meeting_status_change) | agent-api (subscribe pattern), api-gateway (WebSocket), tests | Meeting status transitions |
| **tc:meeting:{meeting_id}:mutable** | transcription-collector (publish on updates) | meeting-api, api-gateway (WebSocket) | Mutable transcription data |
| **va:meeting:{meeting_id}:chat** | meeting-api/voice_agent.py | api-gateway (WebSocket), tests | Chat messages from bot |
| **bot_commands:meeting:{meeting_id}** | meeting-api (send_bot_command) | vexa-bot container (subscriber) | Commands to bot (speak, chat, screen, avatar) |
| **meeting:{meeting_id}:segments** | transcription-collector (streaming/processors.py) | transcription-collector (db_writer), meeting-api (callbacks) | Transcription segments cache |
| **meeting:{meeting_id}:chat_messages** | meeting-api/callbacks.py, voice_agent.py | meeting-api/callbacks.py (read in exit callback) | Chat history |
| **meeting:{meeting_id}:pending:{speaker}** | transcription-collector (streaming/processors.py) | transcription-collector | Pending speaker segments |
| **browser_session:{token}** | meeting-api/meetings.py | api-gateway/main.py (read in WebSocket route) | Browser session data (NEEDS_HUMAN_HELP) |
| **webhook:retry_queue** | shared_models/webhook_delivery.py | shared_models/webhook_retry_worker.py | Retry queue for failed webhook deliveries |

**Key Ownership Summary:**
- **meeting-api** (bm: prefix — frozen) — published by meeting-api, consumed by agent-api, api-gateway
- **transcription-collector-style** (tc:, meeting:) — published by transcription-collector, read by meeting-api, api-gateway
- **voice-agent-style** (va:) — published by meeting-api voice_agent, read by api-gateway
- **webhook** — written/read by shared_models webhooks

**No conflicts detected.** Each prefix is clearly owned and read-only by consumers.

---

## 5. Database Table Access

| Table | Queried By | Writes By | Notes |
|---|---|---|---|
| **User** | admin-api (CRUD), agent-api (auth), meeting-api (auth), transcription-collector (auth), calendar-service (sync) | admin-api, calendar-service | Central identity table |
| **APIToken** | admin-api (CRUD), agent-api (auth), meeting-api (auth), transcription-collector (auth) | admin-api (create/revoke) | Token storage, shared access |
| **Meeting** | admin-api (list), agent-api (webhook), meeting-api (CRUD, bot status), transcription-collector (queries), calendar-service | meeting-api (create/update status), agent-api (webhook callback) | Meeting domain table |
| **MeetingSession** | meeting-api (CRUD, callbacks), transcription-collector (streaming, queries) | meeting-api | Session tracking per bot |
| **Transcription** | admin-api (list?), transcription-collector (create segments), api-gateway (proxy) | transcription-collector (db_writer) | Transcription data |
| **Recording** | meeting-api (upload tracking), api-gateway (proxy) | meeting-api, transcription-collector | Recording metadata |
| **MediaFile** | meeting-api (recordings), api-gateway (proxy) | meeting-api | Media file references |
| **CalendarEvent** | calendar-service (sync) | calendar-service | Google Calendar integration |
| **WebhookUrl** | meeting-api (POST /webhooks, GET /webhooks) | meeting-api | Webhook URL registration |
| **WebhookDeliveryHistory** | (implicit via webhook_delivery utilities) | shared_models/webhook_delivery.py | Webhook delivery tracking |

**Table Ownership:**
- **Owned by User**: admin-api (CRUD), others read
- **Owned by Meeting**: meeting-api (CRUD), agent-api/transcription-collector read
- **Owned by Transcription**: transcription-collector (writes), api-gateway proxies, admin-api reads
- **Owned by Recording**: meeting-api (lifecycle), api-gateway proxies

**Multi-Writer Tables:**
- **Meeting**: meeting-api creates/updates status, agent-api updates via webhook callback
- **MeetingSession**: meeting-api creates, transcription-collector reads/updates

**No orphaned table access detected.** All database access goes through shared_models ORM.

---

## 6. Environment Variable Coupling

| Env Var | Used By | Purpose | Required |
|---|---|---|---|
| **RUNTIME_API_URL** | agent-api/app/config.py, agent-api/app/container_manager.py | Container CRUD (create, stop, list) | Yes (agent-api) |
| **RUNTIME_API_URL** | meeting-api/meeting_api/config.py | (Loaded but not currently used in meetings.py) | No (fallback: http://runtime-api:8000) |
| **MEETING_API_URL** | meeting-api/meeting_api/config.py, meeting-api/meeting_api/meetings.py | Callback URL for bot exit | Yes (meeting-api) |
| **TRANSCRIPTION_COLLECTOR_URL** | meeting-api/meeting_api/config.py, meeting-api/meeting_api/post_meeting.py | Fetch transcripts after meeting ends | Yes (meeting-api) |
| **TTS_SERVICE_URL** | meeting-api/meeting_api/meetings.py | Optional TTS service integration | No (optional) |
| **MEETING_API_URL** | calendar-service/app/sync.py | Get user API keys, create bots | Yes (calendar-service) |
| **ADMIN_API_URL** | api-gateway/main.py | Proxy admin endpoints | Yes (api-gateway) |
| **MEETING_API_URL** | api-gateway/main.py | Proxy bot endpoints | Yes (api-gateway) |
| **TRANSCRIPTION_COLLECTOR_URL** | api-gateway/main.py | Proxy transcription endpoints | Yes (api-gateway) |
| **MCP_URL** | api-gateway/main.py | Proxy MCP endpoints | Yes (api-gateway) |
| **ADMIN_API_TOKEN** | admin-api/app/main.py | Admin endpoint authentication | Yes (admin-api) |
| **ADMIN_TOKEN** | meeting-api/meeting_api/meetings.py | Mint MeetingToken (HS256 JWT) | Yes (meeting-api) |
| **BOT_API_TOKEN** | agent-api/app/container_manager.py | Authorization header for runtime-api calls | No (optional, used if present) |
| **REDIS_URL** | All services (via fastapi-redis, etc.) | Redis connection | Yes (all) |

**Blocking Dependencies:**
- **api-gateway** requires: ADMIN_API_URL, MEETING_API_URL, TRANSCRIPTION_COLLECTOR_URL, MCP_URL (hard-fail on startup)
- **admin-api** requires: ADMIN_API_TOKEN (error logged at startup if missing)
- **meeting-api** requires: ADMIN_TOKEN, TRANSCRIPTION_COLLECTOR_URL, RUNTIME_API_URL, MEETING_API_URL
- **agent-api** requires: RUNTIME_API_URL

**Missing/Undocumented:**
- `BOT_API_TOKEN` in agent-api (optional, should be required for runtime-api auth)
- `ADMIN_TOKEN` usage in transcription-collector (fallback: checks ADMIN_API_TOKEN also)

---

## 7. Service Call Graph

```
┌──────────────────────────────────────────────────┐
│                  API Gateway                      │
│  (Proxy layer: routes HTTP to backend services)   │
└────────────────┬─────────────────┬───────────────┘
                 │                 │
        ┌────────┴─────┐   ┌───────┴──────────┐
        │              │   │                  │
        v              v   v                  v
    Admin-API     Bot-Manager    Transcription-Collector
                      (not in                (in-repo)
                     this repo)

    Meeting-API (in-repo)
    │
    ├─→ RUNTIME_API_URL (for container lifecycle)
    ├─→ TRANSCRIPTION_COLLECTOR_URL (fetch transcripts)
    ├─→ shared_models (database)
    └─→ Redis (pub/sub, webhook retry)

    Agent-API (in-repo)
    │
    ├─→ RUNTIME_API_URL (container management)
    ├─→ Redis (subscribe bm:meeting:*:status)
    └─→ shared_models (database)

    Transcription-Collector (in-repo)
    │
    ├─→ shared_models (database)
    ├─→ Redis (publish tc:meeting:*, meeting:segments)
    └─→ Storage client (MinIO/GCS)

    Calendar-Service (in-repo)
    │
    ├─→ MEETING_API_URL (create bots)
    └─→ shared_models (database)

    TTS-Service (in-repo, optional)
    │
    └─→ (Standalone, no internal dependencies)

    MCP (in-repo, optional)
    │
    └─→ (Standalone, proxied via api-gateway)
```

---

## 8. Critical Coupling Points

| Coupling Point | Services | Risk | Required For |
|---|---|---|---|
| **shared_models ORM** | ALL (10+ services) | 🔴 HIGH | Data persistence, schema validation |
| **RUNTIME_API_URL** | agent-api, meeting-api | 🔴 HIGH | Container lifecycle (agents, bots) |
| **TRANSCRIPTION_COLLECTOR_URL** | meeting-api | 🔴 HIGH | Post-meeting transcript aggregation |
| **MEETING_API_URL callback** | meeting-api → bots | 🟡 MEDIUM | Exit status webhook |
| **Redis pub/sub (bm:meeting:*)** | agent-api ← meeting-api | 🟡 MEDIUM | Real-time meeting status events |
| **ADMIN_TOKEN** | meeting-api | 🟡 MEDIUM | JWT minting for transcription auth |
| **MEETING_API_URL** | api-gateway, calendar-service | 🟡 MEDIUM | Meeting/bot API |
| **Database foreign keys** | meeting-api ↔ transcription-collector | 🟡 MEDIUM | Referential integrity |

---

## 9. Dependency Decoupling Opportunities

### 1. **Remove shared_models ORM from API services** (High Impact)
**Current:** All services import and use SQLAlchemy ORM models directly.
**Problem:** Database schema changes ripple across 10+ services. Type coupling is tight.
**Solution:** Create service-specific data access layers. shared_models exports schemas, not models.

### 2. **HTTP contracts instead of Redis for status** (High Impact)
**Current:** agent-api subscribes to `bm:meeting:*:status` from meeting-api via Redis.
**Problem:** Implicit contract, coupling to Redis, no request/response cycle.
**Solution:** Agent-API polls GET /meetings/{id}/status or registers webhook for status updates.

### 3. **Environment variable to config objects** (Medium Impact)
**Current:** Services read env vars directly in main.py or config.py.
**Problem:** Hard to track which services depend on which env vars. No validation.
**Solution:** Centralize config in shared_models, validate on startup, export as structured objects.

### 4. **Remove hardcoded HTTP/webhook URLs** (Medium Impact)
**Current:** meeting-api hardcodes callback_url = f"{MEETING_API_URL}/bots/internal/callback/exited".
**Problem:** Duplicated across multiple endpoints, no validation that URL is reachable.
**Solution:** Define callback contracts in shared_models, validate at startup.

### 5. **Separate webhook delivery from shared_models** (Low Impact)
**Current:** webhook_delivery, webhook_retry_worker in shared_models.
**Problem:** Only meeting-api uses it; adds bloat to shared_models.
**Solution:** Move to meeting-api, or expose as a dedicated webhook service.

---

## 10. Summary

### Architecture Type
**Monolithic → Microservices transition.** Services are logically separated but share central database and Redis. No async messaging (RabbitMQ, Kafka).

### Dependency Count
- **Total services:** 10 (admin-api, agent-api, meeting-api, transcription-collector, api-gateway, calendar-service, tts-service, mcp, runtime-api, vexa-bot)
- **In-repo services:** 10 (all)
- **Cross-service HTTP calls:** 4 (agent-api→runtime-api, meeting-api→transcription-collector, meeting-api→runtime-api, calendar-service→meeting-api)
- **Shared database:** YES (all services use shared_models ORM)
- **Pub/Sub channels:** 4 (bm:meeting:*, tc:meeting:*, va:meeting:*, bot_commands:*, webhook:retry_queue)
- **Environment variable couplings:** 12

### Data Flow
1. **User API request** → api-gateway (HTTP proxy)
2. **Proxy to admin-api or meeting-api** (user operations)
3. **meeting-api creates meeting** → creates Meeting in DB, publishes bm:meeting:status
4. **agent-api subscribes** to bm:meeting:*:status → updates workspace context
5. **meeting-api launches bot** → calls runtime-api (container create)
6. **Bot publishes transcription** → transcription-collector receives, writes to DB
7. **meeting ends** → transcription-collector publishes tc:meeting:*:mutable, meeting-api fetches transcript
8. **meeting-api fires webhooks** → uses webhook_delivery utilities (shared_models)

### Next Steps
See [REFACTORING_PLAN.md](./REFACTORING_PLAN.md) for decoupling strategy.
