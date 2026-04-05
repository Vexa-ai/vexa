"""Tests for dual-mode auth: gateway headers / standalone API keys."""

import os
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from meeting_api.auth import validate_request, get_user_and_token, UserProxy


# ===================================================================
# Helper: build a fake Request with given headers
# ===================================================================

class _FakeHeaders(dict):
    """Dict subclass that works as both dict and has .get() like starlette headers."""
    pass


def _make_request(headers: dict = None):
    req = MagicMock()
    req.headers = _FakeHeaders(headers or {})
    return req


# ===================================================================
# validate_request
# ===================================================================


class TestValidateRequest:

    @pytest.mark.asyncio
    async def test_gateway_mode_returns_user_info(self):
        """X-User-ID header present → gateway mode, returns correct info."""
        req = _make_request({
            "X-User-ID": "42",
            "X-User-Scopes": "bot,user",
            "X-User-Limits": "10",
        })
        result = await validate_request(req)
        assert result["user_id"] == 42
        assert result["scopes"] == ["bot", "user"]
        assert result["max_concurrent"] == 10

    @pytest.mark.asyncio
    async def test_gateway_mode_defaults(self):
        """X-User-ID with no scopes/limits → uses defaults."""
        req = _make_request({"X-User-ID": "7"})
        result = await validate_request(req)
        assert result["user_id"] == 7
        assert result["scopes"] == [""]
        assert result["max_concurrent"] == 1

    @pytest.mark.asyncio
    async def test_standalone_mode_valid_key(self):
        """API_KEYS configured, valid key → returns user_id=0, open scopes."""
        with patch("meeting_api.auth.API_KEYS", ["key-a", "key-b"]):
            req = _make_request({"X-API-Key": "key-a"})
            result = await validate_request(req)
        assert result["user_id"] == 0
        assert result["scopes"] == ["*"]
        assert result["max_concurrent"] == 999

    @pytest.mark.asyncio
    async def test_standalone_mode_invalid_key_raises_403(self):
        """API_KEYS configured, wrong key → 403."""
        with patch("meeting_api.auth.API_KEYS", ["key-a"]):
            req = _make_request({"X-API-Key": "bad-key"})
            with pytest.raises(HTTPException) as exc_info:
                await validate_request(req)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_standalone_mode_missing_key_raises_403(self):
        """API_KEYS configured, no key → 403."""
        with patch("meeting_api.auth.API_KEYS", ["key-a"]):
            req = _make_request({})
            with pytest.raises(HTTPException) as exc_info:
                await validate_request(req)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_auth_configured_open_access(self):
        """No API_KEYS, no X-User-ID → dev mode, open access."""
        with patch("meeting_api.auth.API_KEYS", []):
            req = _make_request({})
            result = await validate_request(req)
        assert result["user_id"] == 0
        assert result["scopes"] == ["*"]

    @pytest.mark.asyncio
    async def test_gateway_takes_precedence_over_api_key(self):
        """X-User-ID present + API_KEYS configured → gateway mode wins."""
        with patch("meeting_api.auth.API_KEYS", ["key-a"]):
            req = _make_request({"X-User-ID": "99", "X-API-Key": "key-a"})
            result = await validate_request(req)
        assert result["user_id"] == 99


# ===================================================================
# get_user_and_token
# ===================================================================


class TestGetUserAndToken:

    @pytest.mark.asyncio
    async def test_returns_tuple_with_userproxy(self):
        """Returns (api_key, UserProxy) tuple."""
        req = _make_request({"X-User-ID": "5", "X-API-Key": "some-key", "X-User-Limits": "3"})
        api_key, user = await get_user_and_token(req)
        assert api_key == "some-key"
        assert isinstance(user, UserProxy)
        assert user.id == 5
        assert user.max_concurrent_bots == 3

    @pytest.mark.asyncio
    async def test_userproxy_has_backward_compat_fields(self):
        """UserProxy has .email, .data, .scopes for backward compat."""
        req = _make_request({"X-User-ID": "7", "X-User-Scopes": "bot"})
        _, user = await get_user_and_token(req)
        assert user.email == "user-7"
        assert user.data == {}
        assert user.scopes == ["bot"]


# ===================================================================
# UserProxy
# ===================================================================


class TestUserProxy:

    def test_basic_attributes(self):
        u = UserProxy(42, 5, ["bot", "user"])
        assert u.id == 42
        assert u.max_concurrent_bots == 5
        assert u.scopes == ["bot", "user"]
        assert u.email == "user-42"
        assert u.data == {}


# ===================================================================
# Auth via HTTP endpoints (integration-style)
# ===================================================================


class TestAuthViaEndpoints:

    @pytest.mark.asyncio
    async def test_no_auth_dev_mode(self, unauthed_client):
        """No API_KEYS set, no headers → dev mode allows access."""
        with patch("meeting_api.auth.API_KEYS", []):
            resp = await unauthed_client.get("/bots/status")
        # Should succeed (dev mode) or return a non-auth error
        # The actual response depends on downstream logic, but auth should pass
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_health_no_auth_needed(self, client):
        """GET /health does not require auth."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
