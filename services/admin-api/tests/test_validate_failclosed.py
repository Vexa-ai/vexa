"""Tests for /internal/validate fail-closed behavior."""
import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Set required env vars before importing app
os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from httpx import AsyncClient, ASGITransport
from admin_models.database import get_db


def _make_user(user_id=5, email="test@example.com", max_concurrent_bots=3):
    user = MagicMock()
    user.id = user_id
    user.email = email
    user.max_concurrent_bots = max_concurrent_bots
    return user


def _make_api_token(token_value, user_id=5):
    api_token = MagicMock()
    api_token.token = token_value
    api_token.user_id = user_id
    return api_token


def _mock_db_result(row):
    result = MagicMock()
    result.first.return_value = row
    return result


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.mark.asyncio
async def test_validate_no_secret_no_devmode_returns_503(mock_db):
    """INTERNAL_API_SECRET unset + DEV_MODE=false -> 503."""
    with patch("app.main.INTERNAL_API_SECRET", ""), \
         patch("app.main.DEV_MODE", False):
        from app.main import app

        async def override_get_db():
            return mock_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/internal/validate", json={"token": "any"})
            assert resp.status_code == 503
            assert "INTERNAL_API_SECRET" in resp.json()["detail"]
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validate_no_secret_devmode_allowed(mock_db):
    """INTERNAL_API_SECRET unset + DEV_MODE=true -> allowed (passes through to token validation)."""
    token = "vxa_user_abc123"
    user = _make_user()
    api_token = _make_api_token(token)
    mock_db.execute.return_value = _mock_db_result((api_token, user))

    with patch("app.main.INTERNAL_API_SECRET", ""), \
         patch("app.main.DEV_MODE", True):
        from app.main import app

        async def override_get_db():
            return mock_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/internal/validate", json={"token": token})
            assert resp.status_code == 200
            assert resp.json()["user_id"] == 5
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validate_secret_set_correct_header(mock_db):
    """INTERNAL_API_SECRET set + correct X-Internal-Secret header -> 200."""
    token = "vxa_user_abc123"
    user = _make_user()
    api_token = _make_api_token(token)
    mock_db.execute.return_value = _mock_db_result((api_token, user))

    with patch("app.main.INTERNAL_API_SECRET", "s3cret"), \
         patch("app.main.DEV_MODE", False):
        from app.main import app

        async def override_get_db():
            return mock_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/internal/validate",
                    json={"token": token},
                    headers={"X-Internal-Secret": "s3cret"},
                )
            assert resp.status_code == 200
            assert resp.json()["user_id"] == 5
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_validate_secret_set_wrong_header(mock_db):
    """INTERNAL_API_SECRET set + wrong X-Internal-Secret header -> 403."""
    with patch("app.main.INTERNAL_API_SECRET", "s3cret"), \
         patch("app.main.DEV_MODE", False):
        from app.main import app

        async def override_get_db():
            return mock_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/internal/validate",
                    json={"token": "any"},
                    headers={"X-Internal-Secret": "wrong"},
                )
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.clear()
