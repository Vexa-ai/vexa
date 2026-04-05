"""Tests for agent_api.auth — API key authentication."""

import pytest
from unittest.mock import patch, MagicMock

from fastapi import HTTPException

from agent_api.auth import require_api_key


class TestAuthWithApiKey:
    """When API_KEY is configured, auth is enforced."""

    @pytest.mark.asyncio
    async def test_valid_key_passes(self):
        with patch("agent_api.auth.config") as mock_config:
            mock_config.API_KEY = "secret-key-123"
            result = await require_api_key("secret-key-123")
            assert result is None  # no exception = pass

    @pytest.mark.asyncio
    async def test_wrong_key_rejected(self):
        with patch("agent_api.auth.config") as mock_config:
            mock_config.API_KEY = "secret-key-123"
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key("wrong-key")
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_key_rejected(self):
        with patch("agent_api.auth.config") as mock_config:
            mock_config.API_KEY = "secret-key-123"
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key(None)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_key_rejected(self):
        with patch("agent_api.auth.config") as mock_config:
            mock_config.API_KEY = "secret-key-123"
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key("")
            assert exc_info.value.status_code == 403


class TestAuthOpenAccess:
    """When API_KEY is not configured, all requests pass (dev mode)."""

    @pytest.mark.asyncio
    async def test_any_key_passes(self):
        with patch("agent_api.auth.config") as mock_config:
            mock_config.API_KEY = ""
            result = await require_api_key("anything")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_key_passes(self):
        with patch("agent_api.auth.config") as mock_config:
            mock_config.API_KEY = ""
            result = await require_api_key(None)
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_key_passes(self):
        with patch("agent_api.auth.config") as mock_config:
            mock_config.API_KEY = ""
            result = await require_api_key("")
            assert result is None


class TestConstantTimeComparison:
    """Verify that hmac.compare_digest is used for timing-safe comparison."""

    @pytest.mark.asyncio
    async def test_uses_hmac_compare_digest(self):
        with patch("agent_api.auth.config") as mock_config, \
             patch("agent_api.auth.hmac") as mock_hmac:
            mock_config.API_KEY = "secret"
            mock_hmac.compare_digest.return_value = True

            await require_api_key("secret")
            mock_hmac.compare_digest.assert_called_once_with("secret", "secret")

    @pytest.mark.asyncio
    async def test_hmac_false_rejects(self):
        with patch("agent_api.auth.config") as mock_config, \
             patch("agent_api.auth.hmac") as mock_hmac:
            mock_config.API_KEY = "secret"
            mock_hmac.compare_digest.return_value = False

            with pytest.raises(HTTPException) as exc_info:
                await require_api_key("wrong")
            assert exc_info.value.status_code == 403
