"""Shared fixtures for meeting-api tests.

Provides: mock DB, mock Redis, mock httpx (Runtime API), test user/token,
and an httpx.AsyncClient wired to the FastAPI app with all deps overridden.
"""

# --- Environment must be set BEFORE any model imports ---
import os
import sys
from pathlib import Path

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_pass")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-secret")

# Ensure libs/admin-models is importable (needed by collector modules)
_repo = Path(__file__).resolve().parent.parent.parent.parent
_admin_models_path = str(_repo / "libs" / "admin-models")
if _admin_models_path not in sys.path:
    sys.path.insert(0, _admin_models_path)

import json
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from meeting_api.models import Meeting, MeetingSession
from meeting_api.schemas import MeetingStatus
from meeting_api.auth import UserProxy


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

TEST_USER_ID = 5
TEST_USER_EMAIL = "test@example.com"
TEST_API_KEY = "vxa_bot_testkey123"
TEST_MEETING_ID = 42
TEST_SESSION_UID = "sess-abc123"
TEST_CONTAINER_ID = "container-xyz"
TEST_CONTAINER_NAME = "meeting-bot-42-abc"
TEST_PLATFORM = "google_meet"
TEST_NATIVE_MEETING_ID = "abc-defg-hij"


def make_user(**overrides) -> UserProxy:
    defaults = dict(
        user_id=TEST_USER_ID,
        max_concurrent=5,
        scopes=["*"],
    )
    defaults.update(overrides)
    user = UserProxy(defaults["user_id"], defaults["max_concurrent"], defaults["scopes"])
    user.email = overrides.get("email", TEST_USER_EMAIL)
    user.data = overrides.get("data", {})
    return user


def make_meeting(**overrides) -> MagicMock:
    now = datetime.utcnow()
    defaults = dict(
        id=TEST_MEETING_ID,
        user_id=TEST_USER_ID,
        platform=TEST_PLATFORM,
        platform_specific_id=TEST_NATIVE_MEETING_ID,
        native_meeting_id=TEST_NATIVE_MEETING_ID,
        constructed_meeting_url=f"https://meet.google.com/{TEST_NATIVE_MEETING_ID}",
        status=MeetingStatus.REQUESTED.value,
        bot_container_id=None,
        start_time=None,
        end_time=None,
        data={},
        created_at=now,
        updated_at=now,
        user=None,
    )
    defaults.update(overrides)
    m = MagicMock(spec=Meeting)
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def make_session(**overrides) -> MagicMock:
    defaults = dict(
        id=1,
        meeting_id=TEST_MEETING_ID,
        session_uid=TEST_SESSION_UID,
        session_start_time=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    s = MagicMock(spec=MeetingSession)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# Mock DB session
# ---------------------------------------------------------------------------

class MockResult:
    """Simulates SQLAlchemy result objects."""

    def __init__(self, items=None, scalar_value=None):
        self._items = items or []
        self._scalar_value = scalar_value

    def scalars(self):
        return self

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalar(self):
        return self._scalar_value


def _fake_refresh(obj, *args, **kwargs):
    """Simulate DB refresh by populating server-default fields on ORM objects."""
    from meeting_api.models import Meeting as MeetingModel
    if isinstance(obj, MeetingModel):
        now = datetime.utcnow()
        if obj.id is None:
            # Simulate auto-increment ID
            obj.__dict__["id"] = TEST_MEETING_ID
        if obj.created_at is None:
            obj.__dict__["created_at"] = now
        if obj.updated_at is None:
            obj.__dict__["updated_at"] = now


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult())
    db.get = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=_fake_refresh)
    db.rollback = AsyncMock()
    db.flush = AsyncMock()
    db.close = AsyncMock()
    db.delete = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Mock Redis
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.publish = AsyncMock(return_value=1)
    r.set = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.lrange = AsyncMock(return_value=[])
    r.ping = AsyncMock()
    r.close = AsyncMock()
    return r


# ---------------------------------------------------------------------------
# FastAPI app with all external deps mocked
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(mock_db, mock_redis) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient wired to the app with DB, Redis, auth, and Runtime API mocked."""
    from meeting_api.main import app
    from meeting_api import meetings as meetings_mod

    test_user = make_user()

    # Override get_db
    async def override_get_db():
        yield mock_db

    # Override auth
    async def override_auth():
        return (TEST_API_KEY, test_user)

    from meeting_api.database import get_db
    from meeting_api.auth import get_user_and_token

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_user_and_token] = override_auth

    # Inject mock Redis
    meetings_mod.set_redis(mock_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Cleanup
    app.dependency_overrides.clear()
    meetings_mod.set_redis(None)


@pytest_asyncio.fixture
async def unauthed_client(mock_db, mock_redis) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient with NO auth override — for testing auth rejection.

    Sets API_KEYS so that requests without a valid key are rejected (403).
    """
    from meeting_api.main import app
    from meeting_api import meetings as meetings_mod
    import meeting_api.auth as auth_mod

    async def override_get_db():
        yield mock_db

    from meeting_api.database import get_db
    app.dependency_overrides[get_db] = override_get_db
    meetings_mod.set_redis(mock_redis)

    # Enable standalone auth so missing key → 403
    original_keys = auth_mod.API_KEYS
    auth_mod.API_KEYS = ["test-valid-key"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    meetings_mod.set_redis(None)
    auth_mod.API_KEYS = original_keys
