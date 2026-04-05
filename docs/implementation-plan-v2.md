# Implementation Plan v2 — Package Ownership Architecture

> Based on [architecture-proposed.md](./architecture-proposed.md). Every claim below was verified by reading the actual source files.

## Status Quo Summary

| Component | Location | DB models used | Auth mechanism |
|-----------|----------|---------------|----------------|
| runtime-api | `services/runtime-api/` | None (Redis only) | API_KEYS env var, middleware |
| agent-api | `services/agent-api/` | None (Redis only) | API_KEY env var, FastAPI dep |
| meeting-api | `services/meeting-api/` | User, APIToken, Meeting, Transcription, MeetingSession, Recording, MediaFile | Queries User+APIToken via DB join |
| admin-api | `services/admin-api/` | User, APIToken, Meeting, Transcription, MeetingSession | ADMIN_API_TOKEN env var |
| api-gateway | `services/api-gateway/` | None (imports schemas for OpenAPI docs) | Pass-through (forwards X-API-Key) |
| transcription-collector | `services/transcription-collector/` | User, Meeting, Transcription, MeetingSession, APIToken | JWT meeting token + DB queries |
| transcription-service | `services/transcription-service/` | None | API_TOKEN env var |
| tts-service | `services/tts-service/` | None | TTS_API_TOKEN env var |
| shared-models | `libs/shared-models/` | All models, database.py, schemas.py, token_scope.py, webhook_delivery.py, storage.py | N/A |

**Key insight:** runtime-api, agent-api, transcription-service, and tts-service are already standalone (no Postgres). The heavy lifting is meeting-api (absorbs meeting models + TC) and admin-api (keeps user models).

**Migration status:** No alembic migration files exist (`alembic/versions/` is empty). All services use `Base.metadata.create_all(checkfirst=True)`. This makes model splitting much simpler — no migration history to untangle.

---

## Step 1: Move tts-service to packages/ (No breakage)

### What moves

```
services/tts-service/ → services/tts-service/
```

### File changes

- Move entire directory
- Update `deploy/compose/docker-compose.yml`: change build context dockerfile path
- Update `services/README.md`: update location reference

### Models owned

None. tts-service is pure compute (Piper TTS).

### Auth interface

Already has one (`verify_api_key` in `main.py:152`). Reads `TTS_API_TOKEN` env var. Add dual-mode:

```python
# services/tts-service/tts_service/auth.py
async def validate_request(request: Request) -> dict:
    # 1. X-User-ID header (gateway mode)
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return {"user_id": user_id, "scopes": request.headers.get("X-User-Scopes", "").split(",")}
    # 2. X-API-Key (standalone mode)
    api_key = request.headers.get("X-API-Key", "")
    token = os.getenv("TTS_API_TOKEN", "").strip()
    if not token or api_key == token:
        return {"user_id": "anonymous", "scopes": ["*"]}
    raise HTTPException(401, "Invalid or missing API key")
```

### docker-compose.yml (standalone)

```yaml
# services/tts-service/docker-compose.yml
services:
  tts-service:
    build: .
    ports: ["${PORT:-8084}:8084"]
    environment:
      - TTS_API_TOKEN=${TTS_API_TOKEN:-}
      - PIPER_VOICES_DIR=/app/voices
    volumes:
      - tts-voices:/app/voices
volumes:
  tts-voices:
```

### Import changes

None. tts-service has zero imports from other packages.

---

## Step 2: Move transcription-service to packages/ (No breakage)

### What moves

```
services/transcription-service/ → services/transcription-service/
```

### File changes

Same pattern as Step 1. Already standalone — zero shared-models imports.

### Models owned

None. Pure compute (faster-whisper).

### Auth interface

Already has one (`verify_api_token` in `main.py:124`). Add same dual-mode pattern.

### docker-compose.yml (standalone)

Already has three: `docker-compose.yml`, `docker-compose.cpu.yml`, `docker-compose.override.yml`. These move with the directory.

### Import changes

None.

---

## Step 3: Move meeting-api to packages/ (No breakage)

### What moves

```
services/meeting-api/ → services/meeting-api/
```

At this step: same code, new location. Model ownership changes come in Step 6.

### File changes

- Move directory
- Update `deploy/compose/docker-compose.yml`: change build context
- Update `services/README.md`: update location reference

### Import changes

None at this step — meeting-api still imports from `shared_models`.

---

## Step 4: Add X-User-ID header injection to gateway (Additive, no breakage)

### What changes in api-gateway

**File:** `services/api-gateway/main.py`

Currently the gateway just forwards `X-API-Key` headers. We add token validation before forwarding:

```python
# New: validate token and inject headers
async def inject_user_headers(request: Request) -> dict:
    """Call admin-api to validate token, return user info for header injection."""
    api_key = request.headers.get("x-api-key")
    if not api_key:
        return {}

    try:
        resp = await app.state.http_client.post(
            f"{ADMIN_API_URL}/internal/validate",
            json={"token": api_key},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "X-User-ID": str(data["user_id"]),
                "X-User-Scopes": ",".join(data.get("scopes", [])),
                "X-User-Limits": str(data.get("max_concurrent", 1)),
            }
    except Exception:
        pass
    return {}
```

**In `forward_request`** (line 173), after copying headers:
```python
# Inject user identity headers from token validation
user_headers = await inject_user_headers(request)
headers.update(user_headers)
```

### What changes in admin-api

**New endpoint:** `POST /internal/validate`

```python
@app.post("/internal/validate", include_in_schema=False)
async def validate_token(payload: dict, db: AsyncSession = Depends(get_db)):
    """Internal endpoint for gateway token validation."""
    token = payload.get("token", "")
    if not token:
        raise HTTPException(401, "Missing token")

    result = await db.execute(
        select(APIToken, User)
        .join(User, APIToken.user_id == User.id)
        .where(APIToken.token == token)
    )
    row = result.first()
    if not row:
        raise HTTPException(401, "Invalid token")

    api_token, user = row
    scope = parse_token_scope(token)  # from token_scope.py

    return {
        "user_id": user.id,
        "scopes": [scope] if scope else ["admin"],  # legacy tokens = admin
        "max_concurrent": user.max_concurrent_bots,
    }
```

### Why no breakage

- Gateway still forwards `X-API-Key` (downstream services that still do their own auth continue working)
- New headers are additive — old services ignore them
- admin-api's `/internal/validate` is a new endpoint

---

## Step 5: Meeting-api reads X-User-ID from headers (Requires Step 4)

### What changes

**File:** `services/meeting-api/meeting_api/auth.py`

Current auth (`auth.py:18-65`) does a DB join on APIToken + User to get the user object. Replace with header-based auth:

```python
# services/meeting-api/meeting_api/auth.py
"""Dual-mode auth: gateway headers or standalone API keys."""

import os
import hmac
import logging
from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger("meeting_api.auth")

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]


async def validate_request(request: Request) -> dict:
    """Returns {user_id, scopes, limits} or raises 401/403.

    Checks in order:
    1. X-User-ID header (trusted — set by gateway behind reverse proxy)
    2. X-API-Key header (standalone — checked against API_KEYS env var)
    3. Neither → 401
    """
    # Gateway mode: trusted headers
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return {
            "user_id": int(user_id),
            "scopes": request.headers.get("X-User-Scopes", "").split(","),
            "max_concurrent": int(request.headers.get("X-User-Limits", "1")),
        }

    # Standalone mode: API key check
    api_key = request.headers.get("X-API-Key", "")
    if API_KEYS:
        if not api_key or api_key not in API_KEYS:
            raise HTTPException(status_code=403, detail="Invalid or missing API key")
        # In standalone mode, user_id comes from request body or defaults to 0
        return {"user_id": 0, "scopes": ["*"], "max_concurrent": 999}

    # No auth configured — open access (dev mode)
    if not API_KEYS:
        return {"user_id": 0, "scopes": ["*"], "max_concurrent": 999}

    raise HTTPException(status_code=401, detail="Authentication required")


# Backward-compatible wrapper for existing endpoints
async def get_user_and_token(request: Request) -> tuple:
    """Compatibility shim: returns (api_key, user_info) for existing code."""
    info = await validate_request(request)
    api_key = request.headers.get("X-API-Key", "")

    # Create a minimal user-like object for existing code that accesses user.id, user.data
    class UserProxy:
        def __init__(self, user_id, max_concurrent):
            self.id = user_id
            self.max_concurrent_bots = max_concurrent
            self.data = {}
            self.email = f"user-{user_id}"

    return (api_key, UserProxy(info["user_id"], info["max_concurrent"]))
```

### Impact on meeting-api endpoints

**meetings.py (line 29):** Currently `from shared_models.models import User, Meeting, MeetingSession`. After this step:
- `User` import is removed — auth no longer returns User ORM objects
- `Meeting`, `MeetingSession` still imported from shared_models (until Step 6)

**webhooks.py (lines 75-88):** Currently accesses `meeting.user.data.get("webhook_url")`. After this step:
- Webhook URL lookup needs to call admin-api or be stored on the meeting itself
- **Decision:** Store webhook_url in meeting.data at creation time (the gateway/caller provides it)

**recordings.py (line 21):** Currently imports User. After this step:
- `User` import removed — recordings use `user_id` integer

**voice_agent.py (line 15):** Currently imports User. After this step:
- `User` import removed — voice agent uses `user_id` integer from auth

### Endpoints affected

| Endpoint | Current auth | New auth |
|----------|-------------|----------|
| POST /bots | `get_user_and_token` → DB join | `validate_request` → headers |
| DELETE /bots/{p}/{id} | Same | Same |
| GET /bots/status | Same | Same |
| POST /bots/{p}/{id}/speak | Same | Same |
| POST /bots/{p}/{id}/chat | Same | Same |
| GET /recordings | Same | Same |
| POST /internal/callback/* | Meeting token JWT | No change (internal) |

---

## Step 6: Split shared-models — meeting models to meeting-api, agent models to agent-api (Biggest change)

### What goes where

| Model | Current location | Target location | Notes |
|-------|-----------------|-----------------|-------|
| `User` | shared_models.models | libs/admin-models/admin_models/models.py | Admin-api owns |
| `APIToken` | shared_models.models | libs/admin-models/admin_models/models.py | Admin-api owns |
| `Meeting` | shared_models.models | services/meeting-api/meeting_api/models.py | meeting-api owns |
| `Transcription` | shared_models.models | services/meeting-api/meeting_api/models.py | meeting-api owns |
| `MeetingSession` | shared_models.models | services/meeting-api/meeting_api/models.py | meeting-api owns |
| `Recording` | shared_models.models | services/meeting-api/meeting_api/models.py | meeting-api owns |
| `MediaFile` | shared_models.models | services/meeting-api/meeting_api/models.py | meeting-api owns |
| `CalendarEvent` | shared_models.models | services/calendar-service/ or meeting-api | Calendar-service related |

| Module | Current | Target | Notes |
|--------|---------|--------|-------|
| database.py | shared_models | Each package gets its own copy | Minimal: engine + session + get_db |
| schemas.py | shared_models | Split: meeting schemas → meeting-api, admin schemas → admin-models | Platform enum shared via tiny util |
| token_scope.py | shared_models | libs/admin-models | Only admin-api and gateway need this |
| webhook_delivery.py | shared_models | services/meeting-api | Only meeting-api uses webhooks |
| webhook_retry_worker.py | shared_models | services/meeting-api | Only meeting-api uses this |
| storage.py | shared_models | services/meeting-api | Only meeting-api + TC use storage |
| security_headers.py | shared_models | services/api-gateway or shared util | Middleware used by gateway + admin |

### New file: services/meeting-api/meeting_api/models.py

```python
"""Meeting domain models — owned by meeting-api."""
from sqlalchemy import Column, String, Text, Integer, DateTime, Float, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func, text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)  # Just an int, no FK to users in code
    platform = Column(String(100), nullable=False)
    platform_specific_id = Column(String(255), index=True, nullable=True)
    status = Column(String(50), nullable=False, default='requested', index=True)
    bot_container_id = Column(String(255), nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    data = Column(JSONB, nullable=False, default=text("'{}'::jsonb"))
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    transcriptions = relationship("Transcription", back_populates="meeting")
    sessions = relationship("MeetingSession", back_populates="meeting", cascade="all, delete-orphan")
    recordings = relationship("Recording", back_populates="meeting", cascade="all, delete-orphan")

    # FK to users.id exists at DB level only — NOT in SQLAlchemy
    __table_args__ = (
        Index('ix_meeting_user_platform_native_id_created_at', 'user_id', 'platform', 'platform_specific_id', 'created_at'),
        Index('ix_meeting_data_gin', 'data', postgresql_using='gin'),
    )

    # ... (same properties as current: native_meeting_id, constructed_meeting_url)

class Transcription(Base):
    # ... (same as current, but no FK to users)

class MeetingSession(Base):
    # ... (same as current)

class Recording(Base):
    __tablename__ = "recordings"
    # ... (same columns, but user_id is just Integer, no ForeignKey("users.id"))
    user_id = Column(Integer, nullable=False, index=True)  # No FK
    # ... rest same

class MediaFile(Base):
    # ... (same as current)
```

**Critical change:** `user_id` columns become plain `Integer` with no `ForeignKey("users.id")` in the ORM. The FK constraint stays in the database (added via raw SQL migration) but the Python model doesn't know about it.

### New file: services/meeting-api/meeting_api/database.py

```python
"""Database setup for meeting-api standalone or Vexa deployment."""
import os, ssl, logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from .models import Base

# Same DB config pattern as current shared_models.database but with its own Base
# ... (same env var pattern: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD)
```

### New file: libs/admin-models/admin_models/models.py

```python
"""Admin domain models — users and API tokens."""
from sqlalchemy import Column, String, Text, Integer, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func, text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(100))
    image_url = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    max_concurrent_bots = Column(Integer, nullable=False, server_default='1', default=1)
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})

    api_tokens = relationship("APIToken", back_populates="user")

class APIToken(Base):
    __tablename__ = "api_tokens"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="api_tokens")
```

### What happens to shared-models

`libs/shared-models/` is **deleted**. Its contents split to:

| Old file | New location |
|----------|-------------|
| models.py (User, APIToken) | libs/admin-models/admin_models/models.py |
| models.py (Meeting, Transcription, etc.) | services/meeting-api/meeting_api/models.py |
| database.py | Each package gets a minimal copy |
| schemas.py (MeetingCreate, MeetingResponse, Platform, etc.) | services/meeting-api/meeting_api/schemas.py |
| schemas.py (UserCreate, UserResponse, etc.) | libs/admin-models/admin_models/schemas.py |
| token_scope.py | libs/admin-models/admin_models/token_scope.py |
| webhook_delivery.py | services/meeting-api/meeting_api/webhook_delivery.py |
| webhook_retry_worker.py | services/meeting-api/meeting_api/webhook_retry_worker.py |
| storage.py | services/meeting-api/meeting_api/storage.py |
| security_headers.py | libs/admin-models/admin_models/security_headers.py |
| webhook_url.py | services/meeting-api/meeting_api/webhook_url.py |
| scheduler.py, scheduler_worker.py | services/runtime-api/runtime_api/scheduler.py (already there) |

### Import changes by consumer

| Consumer | Old import | New import |
|----------|-----------|------------|
| meeting-api.meetings | `from shared_models.models import User, Meeting` | `from meeting_api.models import Meeting` (User removed) |
| meeting-api.auth | `from shared_models.models import User, APIToken` | Deleted — replaced by header auth |
| meeting-api.recordings | `from shared_models.models import User, Meeting, Recording, MediaFile` | `from meeting_api.models import Meeting, Recording, MediaFile` |
| meeting-api.callbacks | `from shared_models.models import Meeting, MeetingSession` | `from meeting_api.models import Meeting, MeetingSession` |
| meeting-api.webhooks | `from shared_models.models import Meeting, User` | `from meeting_api.models import Meeting` |
| meeting-api.voice_agent | `from shared_models.models import User` | Removed |
| meeting-api.main | `from shared_models.database import init_db` | `from meeting_api.database import init_db` |
| admin-api.main | `from shared_models.models import User, APIToken, Meeting, ...` | `from admin_models.models import User, APIToken` + read-only meeting access |
| TC (folded into meeting-api) | `from shared_models.models import Meeting, Transcription` | `from meeting_api.models import Meeting, Transcription` |
| api-gateway | `from shared_models.schemas import MeetingCreate, ...` | Inline Pydantic models or `from meeting_api.schemas import ...` |

### FK integrity at the DB level

The `meetings.user_id` → `users.id` FK must still exist in the database for referential integrity. But the ORM doesn't model it.

**How to maintain it:**

```sql
-- Run once as a DB migration (raw SQL, not via any package's create_all)
ALTER TABLE meetings
  ADD CONSTRAINT fk_meetings_user_id
  FOREIGN KEY (user_id) REFERENCES users(id);

ALTER TABLE recordings
  ADD CONSTRAINT fk_recordings_user_id
  FOREIGN KEY (user_id) REFERENCES users(id);
```

Each package's `Base.metadata.create_all(checkfirst=True)` creates its own tables. Cross-schema FKs are managed separately via raw SQL that runs once during initial setup.

**Startup order:**
1. admin-api starts → creates `users` + `api_tokens` tables
2. meeting-api starts → creates `meetings`, `transcriptions`, etc. tables (no FK to users)
3. A one-time setup script adds the cross-table FK constraints

### Migration strategy

Since no alembic migrations exist (confirmed: `alembic/versions/` is empty), and all services use `create_all(checkfirst=True)`:

1. **Existing database:** Tables already exist with FK constraints. The new ORM definitions are compatible — they define the same columns, just without `ForeignKey()` declarations in Python. `create_all(checkfirst=True)` is a no-op when tables exist.

2. **New database:** Each package's `create_all()` creates its own tables. A separate init script adds cross-schema FKs.

3. **Per-package alembic (future):** Each package gets its own `alembic/` directory with its own migration history, starting fresh. The shared `alembic.ini` at repo root is replaced by per-package configs.

---

## Step 7: Rename shared-models to libs/admin-models (After Step 6)

### What moves

```
packages/shared-models/ → libs/admin-models/
```

Package name: `admin_models` (was `shared_models`)

### What stays in admin-models

- `admin_models/models.py` — User, APIToken
- `admin_models/schemas.py` — UserCreate, UserResponse, TokenResponse, UserBase, UserUpdate, UserDetailResponse
- `admin_models/database.py` — engine, session factory, get_db, init_db
- `admin_models/token_scope.py` — token prefix parsing
- `admin_models/security_headers.py` — middleware

### Consumers

- admin-api (primary)
- api-gateway (for token scope checking in auth, security headers middleware)
- calendar-service (reads users for OAuth tokens)

### Import changes

```python
# Before
from shared_models.models import User, APIToken
from shared_models.database import get_db
# After
from admin_models.models import User, APIToken
from admin_models.database import get_db
```

---

## Step 8: Fold transcription-collector into meeting-api (Internal refactor)

### What moves

| TC file | Target in meeting-api |
|---------|----------------------|
| `streaming/consumer.py` | `meeting_api/collector/consumer.py` |
| `streaming/processors.py` | `meeting_api/collector/processors.py` |
| `background/db_writer.py` | `meeting_api/collector/db_writer.py` |
| `filters.py` | `meeting_api/collector/filters.py` |
| `filter_config.py` | `meeting_api/collector/filter_config.py` |
| `mapping/speaker_mapper.py` | `meeting_api/collector/speaker_mapper.py` |
| `api/endpoints.py` | Merged into `meeting_api/transcripts.py` (new router) |
| `api/auth.py` | Deleted — meeting-api's auth covers it |
| `config.py` | Merged into `meeting_api/config.py` |
| `main.py` | Deleted — startup tasks move to `meeting_api/main.py` |

### Changes to meeting-api/main.py

Add to startup:

```python
# In meeting_api/main.py startup()
from .collector.consumer import consume_redis_stream, consume_speaker_events_stream, claim_stale_messages
from .collector.db_writer import process_redis_to_postgres
from .collector.filters import TranscriptionFilter

# Ensure Redis stream consumer groups exist
await _ensure_consumer_groups(redis_client)

# Start collector background tasks
transcription_filter = TranscriptionFilter()
asyncio.create_task(process_redis_to_postgres(redis_client, transcription_filter))
asyncio.create_task(consume_redis_stream(redis_client))
asyncio.create_task(consume_speaker_events_stream(redis_client))
```

### Changes to meeting-api/main.py routers

```python
from .transcripts import router as transcripts_router  # Former TC endpoints
app.include_router(transcripts_router)
```

### Impact on docker-compose

One less service:

```yaml
# Before: 2 services
meeting-api:
  ...
transcription-collector:
  ...

# After: 1 service (meeting-api does both)
meeting-api:
  ...
  environment:
    # Former TC config now in meeting-api
    - REDIS_STREAM_NAME=transcription_segments
    - REDIS_CONSUMER_GROUP=collector_group
    - BACKGROUND_TASK_INTERVAL=10
    - IMMUTABILITY_THRESHOLD=30
```

### Import changes in merged code

| Old import | New import |
|-----------|------------|
| `from shared_models.database import async_session_local` | `from meeting_api.database import async_session_local` |
| `from shared_models.models import Meeting, Transcription` | `from meeting_api.models import Meeting, Transcription` |
| `from config import REDIS_STREAM_NAME, ...` | `from meeting_api.config import REDIS_STREAM_NAME, ...` |

### What TC endpoints become in meeting-api

| TC endpoint | meeting-api endpoint | Notes |
|-------------|---------------------|-------|
| `GET /health` | Merged into meeting-api's `/health` | Add Redis stream health |
| `GET /meetings` | Already exists in meeting-api | TC's version removed |
| `GET /transcripts/{platform}/{id}` | Already exists in meeting-api | TC's version removed |
| `GET /internal/transcripts/{id}` | `GET /internal/transcripts/{id}` | Kept as-is |
| `PATCH /meetings/{platform}/{id}` | Already exists in meeting-api | TC's version removed |
| `DELETE /meetings/{platform}/{id}` | Already exists in meeting-api | TC's version removed |
| `POST /ws/authorize-subscribe` | `POST /ws/authorize-subscribe` | Moved to meeting-api |

### Tests that move

```
services/transcription-collector/tests/ → services/meeting-api/tests/collector/
```

Files: `test_config.py`, `test_filters.py`, `test_processors.py`, `test_db_writer.py`, `test_endpoints.py`, `conftest.py`

---

## Step 9: admin-api reads meeting data via API, not direct DB (After Step 6)

### Current state

admin-api (`services/admin-api/app/main.py:17`) imports `Meeting, Transcription, MeetingSession` directly and queries them for analytics endpoints.

### Target state

admin-api only owns `users` and `api_tokens`. For meeting data:
- Admin dashboard analytics calls meeting-api's `/internal/` endpoints
- Or: admin-api has **read-only** access to meeting tables via raw SQL (no ORM models)

### Pragmatic approach

Since admin-api needs meeting stats for the admin dashboard, keep read-only SQL queries:

```python
# admin-api: read-only meeting stats without importing Meeting model
async def get_meeting_count(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(text("SELECT COUNT(*) FROM meetings WHERE user_id = :uid"), {"uid": user_id})
    return result.scalar() or 0
```

This avoids model imports while keeping admin-api's analytics working. The tables are in the same Postgres instance — admin-api just doesn't own them.

---

## Auth flow — Complete specification

### Gateway flow (Vexa deployment)

```
Client → Gateway → admin-api POST /internal/validate
                  ← {user_id, scopes, max_concurrent}
         Gateway → meeting-api/agent-api (with X-User-ID, X-User-Scopes, X-User-Limits headers)
```

### Standalone flow (someone installs just meeting-api)

```
Deployer sets: API_KEYS=key1,key2,key3
Client → meeting-api (X-API-Key: key1)
         meeting-api checks key against API_KEYS env
         user_id from request body (or defaults to 0)
```

### Auth interface in each package

```python
# Identical pattern in: meeting-api, agent-api, tts-service, transcription-service
async def validate_request(request: Request) -> dict:
    """Returns {user_id, scopes, limits} or raises 401/403."""
    # 1. Gateway headers (trusted reverse proxy)
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return {
            "user_id": user_id,
            "scopes": request.headers.get("X-User-Scopes", "").split(","),
            "max_concurrent": int(request.headers.get("X-User-Limits", "1")),
        }
    # 2. Standalone API key
    api_key = request.headers.get("X-API-Key", "")
    if API_KEYS and api_key in API_KEYS:
        return {"user_id": "0", "scopes": ["*"], "max_concurrent": 999}
    # 3. No auth configured (dev mode)
    if not API_KEYS:
        return {"user_id": "0", "scopes": ["*"], "max_concurrent": 999}
    raise HTTPException(401, "Authentication required")
```

**runtime-api already has this pattern** — `APIKeyMiddleware` in `main.py:119`.

**agent-api already has this pattern** — `require_api_key` in `auth.py:20`.

---

## Testing strategy

### Per-package unit tests

| Package | What to mock | Key test scenarios |
|---------|-------------|-------------------|
| runtime-api | Backend (Docker/K8s/Process) | Container CRUD, idle management, profile loading, callbacks |
| agent-api | runtime-api HTTP calls, Redis | Chat turns, session management, workspace sync |
| meeting-api | runtime-api HTTP calls, Redis | Bot lifecycle, transcription collection, webhooks, recordings |
| transcription-service | Whisper model | Audio transcription, backpressure, tier priority |
| tts-service | Piper voice models | Voice synthesis, resampling, voice resolution |

### Per-package integration tests (standalone docker-compose)

**runtime-api** (already has `docker-compose.yml`):
```yaml
services:
  runtime-api:  # the package
  redis:        # dependency
```
Tests: Create container, list, delete, heartbeat, profiles.

**meeting-api** (new `docker-compose.test.yml`):
```yaml
services:
  meeting-api:   # the package
  redis:         # dependency
  postgres:      # dependency (for meeting models)
  runtime-api:   # dependency (mocked or real)
```
Tests: POST /bots (create meeting), GET /bots/status, webhook delivery, transcript collection, recording upload.

**agent-api** (new `docker-compose.test.yml`):
```yaml
services:
  agent-api:    # the package
  redis:        # dependency
  runtime-api:  # dependency
```
Tests: POST /api/chat, session management, workspace files.

**transcription-service** (already has `docker-compose.yml`):
```yaml
services:
  transcription-service:  # the package
  # no dependencies
```
Tests: POST /v1/audio/transcriptions with WAV file, backpressure 503.

**tts-service** (new `docker-compose.yml`):
```yaml
services:
  tts-service:  # the package
  # no dependencies
```
Tests: POST /v1/audio/speech, voice listing.

### Contract tests (frozen API shapes)

| Package | Contract | Location |
|---------|----------|----------|
| meeting-api | POST /bots request/response | `tests/contracts/test_bot_contracts.py` |
| meeting-api | Callback payloads | `tests/contracts/test_callback_contracts.py` |
| meeting-api | Webhook payloads | `tests/contracts/test_webhook_contracts.py` |
| meeting-api | Transcript response | `tests/contracts/test_transcript_contracts.py` |
| runtime-api | Container CRUD | `tests/contracts/test_container_contracts.py` |
| agent-api | Chat SSE format | `tests/contracts/test_chat_contracts.py` |

### CI pipeline (GitHub Actions)

```yaml
# .github/workflows/test-{package}.yml — one per package
name: Test {package}
on:
  push:
    paths:
      - 'packages/{package}/**'
      - '.github/workflows/test-{package}.yml'
  pull_request:
    paths:
      - 'packages/{package}/**'

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Start dependencies
        run: docker compose -f packages/{package}/docker-compose.test.yml up -d
      - name: Run tests
        run: docker compose -f packages/{package}/docker-compose.test.yml run --rm {package} pytest
      - name: Cleanup
        run: docker compose -f packages/{package}/docker-compose.test.yml down -v
```

---

## Risk assessment

### What breaks during migration

| Step | What could break | Severity | Mitigation |
|------|-----------------|----------|------------|
| 1 (tts move) | Build paths in docker-compose | Low | Test build before merging |
| 2 (transcription move) | Same | Low | Same |
| 3 (meeting-api move) | Same | Low | Same |
| 4 (gateway headers) | Token validation latency | Medium | Cache validated tokens in gateway (Redis, 60s TTL) |
| 5 (meeting auth) | Webhook URL lookup (currently from User.data) | High | Store webhook_url in meeting.data at creation time |
| 5 (meeting auth) | max_concurrent_bots enforcement | Medium | X-User-Limits header carries it |
| 6 (model split) | Any missed import | High | Grep for `from shared_models` — must find zero after split |
| 6 (model split) | ORM relationship breakage | High | Test create_all on clean database |
| 7 (rename) | Any missed import path | Medium | Grep + test |
| 8 (TC fold) | Redis stream consumer reliability | Medium | TC's existing tests catch this |
| 9 (admin read-only) | Admin dashboard analytics | Low | Raw SQL queries work regardless of model ownership |

### Data migration needs

**Existing Postgres:** No schema changes needed. The tables stay the same — only the ORM definitions change (removing `ForeignKey()` from Python, keeping FK constraints in DB). `create_all(checkfirst=True)` is a no-op.

**Webhook URL:** Currently stored in `User.data.webhook_url`. After migration, meeting-api can't read User.data. Solution:
1. At meeting creation time, gateway injects webhook_url from the user into the request
2. meeting-api stores it in `meeting.data.webhook_config`
3. **Backfill:** One-time script copies webhook_url from users.data to all active meetings' data

### Rollback plan per step

| Step | Rollback |
|------|----------|
| 1-3 (moves) | Move directories back. Git revert. |
| 4 (gateway) | Remove header injection code. Downstream still checks X-API-Key directly. |
| 5 (auth) | Restore old auth.py from git. Add shared_models back to meeting-api deps. |
| 6 (model split) | Restore shared_models. Point imports back. Git revert of model files. |
| 7 (rename) | Rename back. |
| 8 (TC fold) | Un-merge: restore transcription-collector service, remove collector/ from meeting-api. |

### Parallel vs sequential execution

```
Parallel group A (independent):
  Step 1: Move tts-service
  Step 2: Move transcription-service
  Step 3: Move meeting-api (location only)

Sequential (dependency chain):
  Step 4: Gateway header injection (needs admin-api /internal/validate)
  Step 5: Meeting-api reads headers (needs Step 4 deployed)
  Step 6: Split shared-models (needs Step 5 working — no more User imports in meeting-api)
  Step 7: Rename to admin-models (needs Step 6 complete)

Parallel group B (after Step 6):
  Step 8: Fold TC into meeting-api
  Step 9: Admin-api read-only meeting access
```

**Estimated execution:** Steps 1-3 are one PR. Step 4 is one PR. Step 5 is one PR. Step 6 is the big PR. Steps 7-9 are smaller follow-up PRs.

---

## Per-package docker-compose summary

### runtime-api (already exists)

```yaml
services:
  runtime-api:
    build: .
    ports: ["8090:8090"]
    environment: [REDIS_URL, ORCHESTRATOR_BACKEND, DOCKER_HOST, PROFILES_PATH, API_KEYS]
    volumes: [/var/run/docker.sock]
    depends_on: [redis]
  redis:
    image: redis:7-alpine
```

### agent-api (new)

```yaml
services:
  agent-api:
    build: .
    ports: ["8100:8100"]
    environment: [REDIS_URL, RUNTIME_API_URL, API_KEY, AGENT_IMAGE]
    depends_on: [redis, runtime-api]
  runtime-api:
    image: ghcr.io/vexa/runtime-api:latest  # Or build from ../runtime-api
    depends_on: [redis]
  redis:
    image: redis:7-alpine
```

### meeting-api (new)

```yaml
services:
  meeting-api:
    build: .
    ports: ["8080:8080"]
    environment:
      - REDIS_URL=redis://redis:6379/0
      - RUNTIME_API_URL=http://runtime-api:8090
      - DB_HOST=postgres
      - DB_PORT=5432
      - DB_NAME=vexa
      - DB_USER=postgres
      - DB_PASSWORD=postgres
      - DB_SSL_MODE=disable
      - API_KEYS=test-key-1
      # Collector config
      - REDIS_STREAM_NAME=transcription_segments
      - REDIS_CONSUMER_GROUP=collector_group
    depends_on: [redis, postgres, runtime-api]
  runtime-api:
    image: ghcr.io/vexa/runtime-api:latest
    depends_on: [redis]
  postgres:
    image: postgres:16-alpine
    environment: [POSTGRES_DB=vexa, POSTGRES_USER=postgres, POSTGRES_PASSWORD=postgres]
  redis:
    image: redis:7-alpine
```

### transcription-service (existing, moves to packages/)

```yaml
services:
  transcription-service:
    build: .
    ports: ["8083:8000"]
    environment: [MODEL_SIZE, DEVICE, COMPUTE_TYPE, API_TOKEN]
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
```

### tts-service (new)

```yaml
services:
  tts-service:
    build: .
    ports: ["8084:8084"]
    environment: [TTS_API_TOKEN, PIPER_VOICES_DIR=/app/voices]
    volumes: [tts-voices:/app/voices]
volumes:
  tts-voices:
```

---

## Execution order summary

| Phase | Steps | PR scope | Can break prod? |
|-------|-------|----------|-----------------|
| **Phase A** | 1, 2, 3 | Directory moves + build path updates | No |
| **Phase B** | 4 | Gateway header injection + admin /internal/validate | No (additive) |
| **Phase C** | 5 | Meeting-api header auth + webhook URL migration | Yes — needs Phase B deployed |
| **Phase D** | 6, 7 | Model split + shared-models rename | Yes — biggest change |
| **Phase E** | 8, 9 | TC fold + admin read-only | No (internal refactor) |

Each phase is a PR. Phase D is the critical one — it touches the most files and has the highest blast radius. It should be preceded by a comprehensive `grep -r "from shared_models"` to find every import that needs updating.
