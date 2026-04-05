"""Tests for gateway auth enforcement and header injection.

Verifies:
- Valid X-API-Key -> downstream gets X-User-ID/X-User-Scopes/X-User-Limits
- Invalid X-API-Key -> 401 returned, request NOT forwarded
- Missing X-API-Key -> 401 returned, request NOT forwarded
- Spoofed X-User-ID stripped before forwarding
- Cache hit -> admin-api not called twice for same token
- Public routes (/) -> no auth needed
- Auth routes (/auth/*) -> no API key required
- Admin routes with X-Admin-API-Key -> forwarded (admin-api validates)
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

# conftest.py sets env vars and sys.path
from main import app, forward_request, _resolve_token


def _make_request(headers: dict = None, body: bytes = b""):
    """Create a mock Starlette Request."""
    req = AsyncMock()
    req.headers = httpx.Headers(headers or {})
    req.query_params = {}
    req.body = AsyncMock(return_value=body)
    return req


def _make_validate_response(status_code=200, user_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = user_data or {}
    return resp


class TestHeaderStripping:
    """Spoofed identity headers are stripped before forwarding."""

    @pytest.mark.asyncio
    async def test_strips_spoofed_x_user_id(self):
        """Client-supplied X-User-ID is removed (valid token case)."""
        captured_headers = {}
        user_data = {"user_id": 5, "scopes": ["bot"], "max_concurrent": 3, "email": "test@x.com"}

        async def mock_request(method, url, headers=None, params=None, content=None):
            captured_headers.update(headers or {})
            resp = MagicMock()
            resp.content = b"{}"
            resp.status_code = 200
            resp.headers = {}
            return resp

        client = AsyncMock()
        client.request = mock_request
        client.post = AsyncMock(return_value=_make_validate_response(200, user_data))

        app.state.redis = None

        req = _make_request(headers={
            "x-api-key": "vxa_bot_abc123",
            "x-user-id": "SPOOFED_999",
            "x-user-scopes": "admin",
            "x-user-limits": "100",
        })

        await forward_request(client, "GET", "http://meeting-api:8000/bots", req)

        assert captured_headers.get("x-user-id") == "5"  # From validated token, not spoofed
        assert captured_headers.get("x-user-scopes") == "bot"
        assert captured_headers.get("x-user-limits") == "3"


class TestHeaderInjection:
    """Valid tokens produce X-User-ID/X-User-Scopes/X-User-Limits headers."""

    @pytest.mark.asyncio
    async def test_valid_token_injects_headers(self):
        """Successful validation injects identity headers."""
        captured_headers = {}
        user_data = {"user_id": 5, "scopes": ["bot"], "max_concurrent": 3, "email": "test@x.com"}

        async def mock_request(method, url, headers=None, params=None, content=None):
            captured_headers.update(headers or {})
            resp = MagicMock()
            resp.content = b"{}"
            resp.status_code = 200
            resp.headers = {}
            return resp

        client = AsyncMock()
        client.request = mock_request
        client.post = AsyncMock(return_value=_make_validate_response(200, user_data))

        # No Redis in test
        app.state.redis = None

        req = _make_request(headers={"x-api-key": "vxa_bot_abc123"})
        await forward_request(client, "GET", "http://meeting-api:8000/bots", req)

        assert captured_headers["x-user-id"] == "5"
        assert captured_headers["x-user-scopes"] == "bot"
        assert captured_headers["x-user-limits"] == "3"


class TestFailClosed:
    """Auth is a gate: invalid/missing tokens return 401, NOT forwarded."""

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self):
        """Failed validation returns 401 — request is NOT forwarded downstream."""
        client = AsyncMock()
        client.request = AsyncMock()  # Should NOT be called
        client.post = AsyncMock(return_value=_make_validate_response(401))

        app.state.redis = None

        req = _make_request(headers={"x-api-key": "bad_token"})
        resp = await forward_request(client, "GET", "http://meeting-api:8000/bots", req)

        assert resp.status_code == 401
        body = json.loads(resp.body)
        assert "Invalid API key" in body["detail"]
        client.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_token_returns_401(self):
        """Missing API key returns 401 — request is NOT forwarded downstream."""
        client = AsyncMock()
        client.request = AsyncMock()  # Should NOT be called

        app.state.redis = None

        req = _make_request(headers={})
        resp = await forward_request(client, "GET", "http://meeting-api:8000/bots", req)

        assert resp.status_code == 401
        body = json.loads(resp.body)
        assert "Missing API key" in body["detail"]
        client.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_token_with_require_auth_false(self):
        """When require_auth=False, missing API key is allowed (e.g. /auth routes)."""
        captured_headers = {}

        async def mock_request(method, url, headers=None, params=None, content=None):
            captured_headers.update(headers or {})
            resp = MagicMock()
            resp.content = b"{}"
            resp.status_code = 200
            resp.headers = {}
            return resp

        client = AsyncMock()
        client.request = mock_request

        req = _make_request(headers={})
        resp = await forward_request(client, "POST", "http://meeting-api:8000/auth/login", req, require_auth=False)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_route_forwards_without_client_key(self):
        """Admin routes use X-Admin-API-Key, not X-API-Key — always forwarded to admin-api."""
        import os
        admin_url = os.environ.get("ADMIN_API_URL", "http://admin-api:8000")
        captured_headers = {}

        async def mock_request(method, url, headers=None, params=None, content=None):
            captured_headers.update(headers or {})
            resp = MagicMock()
            resp.content = b"{}"
            resp.status_code = 200
            resp.headers = {}
            return resp

        client = AsyncMock()
        client.request = mock_request

        req = _make_request(headers={"x-admin-api-key": "admin_secret_123"})
        resp = await forward_request(client, "GET", f"{admin_url}/admin/users", req)

        assert resp.status_code == 200
        assert captured_headers.get("x-admin-api-key") == "admin_secret_123"


class TestTokenCache:
    """Redis cache prevents repeated admin-api calls."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_admin_api(self):
        """Cached token data means admin-api is NOT called."""
        user_data = {"user_id": 7, "scopes": ["user"], "max_concurrent": 1, "email": "cached@x.com"}

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(user_data))
        app.state.redis = mock_redis

        client = AsyncMock()
        # client.post should NOT be called if cache hit
        client.post = AsyncMock()

        result = await _resolve_token(client, "vxa_user_cachedtoken123")

        assert result == user_data
        client.post.assert_not_called()
        mock_redis.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_admin_api_and_caches(self):
        """Cache miss calls admin-api and stores result."""
        user_data = {"user_id": 7, "scopes": ["user"], "max_concurrent": 1, "email": "new@x.com"}

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        app.state.redis = mock_redis

        client = AsyncMock()
        client.post = AsyncMock(return_value=_make_validate_response(200, user_data))

        result = await _resolve_token(client, "vxa_user_newtoken12345")

        assert result == user_data
        client.post.assert_called_once()
        mock_redis.set.assert_called_once()
        # Verify TTL is 60 seconds
        call_args = mock_redis.set.call_args
        assert call_args.kwargs.get("ex") == 60 or (len(call_args.args) >= 3 and call_args.args[2] == 60) or call_args[1].get("ex") == 60
