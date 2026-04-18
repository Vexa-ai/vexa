"""Tests for POST /internal/validate endpoint."""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Set required env vars before importing app
os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
os.environ.setdefault("INTERNAL_API_SECRET", "test-internal-secret")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from httpx import AsyncClient, ASGITransport
from app.main import app
from admin_models.database import get_db

INTERNAL_SECRET = "test-internal-secret"
INTERNAL_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}


def _make_user(user_id=5, email="test@example.com", max_concurrent_bots=3, data=None):
    user = MagicMock()
    user.id = user_id
    user.email = email
    user.max_concurrent_bots = max_concurrent_bots
    user.data = data if data is not None else {}
    return user


def _make_api_token(token_value, user_id=5):
    api_token = MagicMock()
    api_token.token = token_value
    api_token.user_id = user_id
    api_token.expires_at = None
    return api_token


def _mock_db_result(row):
    result = MagicMock()
    result.first.return_value = row
    return result


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_validate_valid_scoped_token(mock_db):
    """Valid vxa_ prefixed token returns 200 with user_id and scopes."""
    token = "vxa_bot_abc123def456"
    user = _make_user()
    api_token = _make_api_token(token)
    mock_db.execute.return_value = _mock_db_result((api_token, user))

    async def override_get_db():
        return mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.main.INTERNAL_API_SECRET", INTERNAL_SECRET):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/internal/validate",
                    json={"token": token},
                    headers=INTERNAL_HEADERS,
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["user_id"] == 5
            assert data["scopes"] == ["bot"]
            assert data["max_concurrent"] == 3
            assert data["email"] == "test@example.com"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validate_legacy_token(mock_db):
    """Legacy token (no vxa_ prefix) returns scopes: ["legacy"]."""
    token = "legacy_token_no_prefix_here"
    user = _make_user()
    api_token = _make_api_token(token)
    mock_db.execute.return_value = _mock_db_result((api_token, user))

    async def override_get_db():
        return mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.main.INTERNAL_API_SECRET", INTERNAL_SECRET):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/internal/validate",
                    json={"token": token},
                    headers=INTERNAL_HEADERS,
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["scopes"] == ["legacy"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validate_invalid_token(mock_db):
    """Invalid token returns 401."""
    mock_db.execute.return_value = _mock_db_result(None)

    async def override_get_db():
        return mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.main.INTERNAL_API_SECRET", INTERNAL_SECRET):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/internal/validate",
                    json={"token": "bad_token"},
                    headers=INTERNAL_HEADERS,
                )

            assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validate_missing_token(mock_db):
    """Missing token field returns 401."""
    async def override_get_db():
        return mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.main.INTERNAL_API_SECRET", INTERNAL_SECRET):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/internal/validate",
                    json={},
                    headers=INTERNAL_HEADERS,
                )

            assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validate_returns_webhook_config(mock_db):
    """When user has webhook config in data, validate response includes it."""
    token = "vxa_bot_abc123def456"
    user = _make_user(data={
        "webhook_url": "https://example.com/hook",
        "webhook_secret": "whsec_test123",
        "webhook_events": {"meeting.completed": True, "meeting.started": True},
    })
    api_token = _make_api_token(token)
    mock_db.execute.return_value = _mock_db_result((api_token, user))

    async def override_get_db():
        return mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.main.INTERNAL_API_SECRET", INTERNAL_SECRET):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/internal/validate",
                    json={"token": token},
                    headers=INTERNAL_HEADERS,
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["webhook_url"] == "https://example.com/hook"
            assert data["webhook_secret"] == "whsec_test123"
            assert data["webhook_events"] == {"meeting.completed": True, "meeting.started": True}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validate_no_webhook_when_not_configured(mock_db):
    """When user has no webhook config, validate response omits webhook fields."""
    token = "vxa_bot_abc123def456"
    user = _make_user(data={})
    api_token = _make_api_token(token)
    mock_db.execute.return_value = _mock_db_result((api_token, user))

    async def override_get_db():
        return mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.main.INTERNAL_API_SECRET", INTERNAL_SECRET):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/internal/validate",
                    json={"token": token},
                    headers=INTERNAL_HEADERS,
                )

            assert resp.status_code == 200
            data = resp.json()
            assert "webhook_url" not in data
            assert "webhook_secret" not in data
            assert "webhook_events" not in data
    finally:
        app.dependency_overrides.clear()
