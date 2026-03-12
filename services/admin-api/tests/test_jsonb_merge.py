"""Tests for admin-api PATCH user.data JSONB merge behavior."""

import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

# Set required env vars before importing app
os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from app.main import app, verify_admin_token
from shared_models.database import get_db


# --- Fixtures ---

def make_fake_user(user_id=1, data=None, email="test@example.com", name="Test"):
    """Create a mock User object."""
    user = MagicMock()
    user.id = user_id
    user.email = email
    user.name = name
    user.image_url = None
    user.max_concurrent_bots = 1
    user.data = data
    user.created_at = "2025-01-01T00:00:00"
    user.meetings = []
    user.api_tokens = []
    return user


def make_mock_db(fake_user):
    """Create a mock async DB session that returns fake_user from queries."""
    db = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = fake_user
    result.scalars.return_value = scalars
    db.execute.return_value = result
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda u: None)
    return db


async def noop_verify_admin():
    """No-op override for admin token verification in tests."""
    return None


# --- Tests ---

@pytest.mark.asyncio
async def test_patch_merges_new_key_into_existing_data():
    """PATCH with {b: 2} when existing data is {a: 1} -> {a: 1, b: 2}."""
    fake_user = make_fake_user(data={"a": 1})
    mock_db = make_mock_db(fake_user)

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[verify_admin_token] = noop_verify_admin

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/admin/users/1",
                json={"data": {"b": 2}},
            )
        assert resp.status_code == 200, resp.text
        assert fake_user.data == {"a": 1, "b": 2}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_overwrites_existing_key():
    """PATCH with {a: 2} when existing data is {a: 1} -> {a: 2}."""
    fake_user = make_fake_user(data={"a": 1})
    mock_db = make_mock_db(fake_user)

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[verify_admin_token] = noop_verify_admin

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/admin/users/1",
                json={"data": {"a": 2}},
            )
        assert resp.status_code == 200, resp.text
        assert fake_user.data == {"a": 2}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_empty_data_preserves_existing():
    """PATCH with data: {} preserves existing data."""
    fake_user = make_fake_user(data={"a": 1, "b": 2})
    mock_db = make_mock_db(fake_user)

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[verify_admin_token] = noop_verify_admin

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/admin/users/1",
                json={"data": {}},
            )
        assert resp.status_code == 200, resp.text
        assert fake_user.data == {"a": 1, "b": 2}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_data_when_user_data_is_none():
    """PATCH with data when user.data is None -> works without error."""
    fake_user = make_fake_user(data=None)
    mock_db = make_mock_db(fake_user)

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[verify_admin_token] = noop_verify_admin

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/admin/users/1",
                json={"data": {"a": 1}},
            )
        assert resp.status_code == 200, resp.text
        assert fake_user.data == {"a": 1}
    finally:
        app.dependency_overrides.clear()
