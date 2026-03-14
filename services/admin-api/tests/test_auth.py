"""Tests for admin-api authentication and authorization logic."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Set required env vars before importing app
os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
os.environ.setdefault("ANALYTICS_API_TOKEN", "test-analytics-token")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")

from fastapi import HTTPException
from app.main import app, verify_admin_token, verify_analytics_or_admin_token


# --- verify_admin_token tests ---

@pytest.mark.asyncio
async def test_verify_admin_token_rejects_missing_key():
    """Requests without X-Admin-API-Key are rejected with 403."""
    with pytest.raises(HTTPException) as exc_info:
        await verify_admin_token(admin_api_key=None)
    assert exc_info.value.status_code == 403
    assert "Invalid or missing" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_admin_token_rejects_wrong_key():
    """Requests with an incorrect key are rejected with 403."""
    with pytest.raises(HTTPException) as exc_info:
        await verify_admin_token(admin_api_key="wrong-key")
    assert exc_info.value.status_code == 403
    assert "Invalid or missing" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_admin_token_rejects_empty_string():
    """Empty string key is rejected with 403."""
    with pytest.raises(HTTPException) as exc_info:
        await verify_admin_token(admin_api_key="")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_admin_token_accepts_correct_key():
    """Requests with the correct admin key pass without error."""
    # Should not raise
    result = await verify_admin_token(admin_api_key="test-admin-token")
    assert result is None  # Function returns None on success


@pytest.mark.asyncio
async def test_verify_admin_token_fails_when_env_not_set():
    """When ADMIN_API_TOKEN env var is not set, returns 500."""
    import app.main as main_module
    original = main_module.ADMIN_API_TOKEN
    try:
        main_module.ADMIN_API_TOKEN = None
        with pytest.raises(HTTPException) as exc_info:
            await verify_admin_token(admin_api_key="any-key")
        assert exc_info.value.status_code == 500
        assert "not configured" in exc_info.value.detail
    finally:
        main_module.ADMIN_API_TOKEN = original


# --- verify_analytics_or_admin_token tests ---

@pytest.mark.asyncio
async def test_analytics_auth_rejects_missing_key():
    """Analytics endpoint rejects requests with no key."""
    with pytest.raises(HTTPException) as exc_info:
        await verify_analytics_or_admin_token(api_key=None)
    assert exc_info.value.status_code == 403
    assert "Missing API key" in exc_info.value.detail


@pytest.mark.asyncio
async def test_analytics_auth_rejects_wrong_key():
    """Analytics endpoint rejects requests with an incorrect key."""
    with pytest.raises(HTTPException) as exc_info:
        await verify_analytics_or_admin_token(api_key="wrong-key")
    assert exc_info.value.status_code == 403
    assert "Invalid API key" in exc_info.value.detail


@pytest.mark.asyncio
async def test_analytics_auth_accepts_admin_key():
    """Analytics endpoint accepts the admin token."""
    result = await verify_analytics_or_admin_token(api_key="test-admin-token")
    assert result is None


@pytest.mark.asyncio
async def test_analytics_auth_accepts_analytics_key():
    """Analytics endpoint accepts the analytics-only token."""
    result = await verify_analytics_or_admin_token(api_key="test-analytics-token")
    assert result is None


@pytest.mark.asyncio
async def test_analytics_auth_rejects_empty_string():
    """Analytics endpoint rejects empty string key."""
    with pytest.raises(HTTPException) as exc_info:
        await verify_analytics_or_admin_token(api_key="")
    assert exc_info.value.status_code == 403


# --- Integration: TestClient auth enforcement on actual routes ---

@pytest.mark.asyncio
async def test_admin_route_rejects_request_without_header():
    """Admin routes return 403 when X-Admin-API-Key header is missing."""
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_route_rejects_request_with_wrong_header():
    """Admin routes return 403 when X-Admin-API-Key header has wrong value."""
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/admin/users",
            headers={"X-Admin-API-Key": "wrong-token"},
        )
    assert resp.status_code == 403
