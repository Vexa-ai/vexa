"""Tests for auto-create auth flow."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


@pytest.mark.asyncio
async def test_auth_cache_hit(mock_redis, mock_tg_user):
    """When Redis has a cached token, use it directly."""
    mock_redis.get = AsyncMock(return_value="42:tok_abc123")

    with patch("bot.get_redis", return_value=mock_redis):
        from bot import get_or_create_auth
        user_id, token = await get_or_create_auth(mock_tg_user)

    assert user_id == "42"
    assert token == "tok_abc123"
    mock_redis.get.assert_awaited_once_with("telegram:12345")
    mock_redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_cache_miss_creates_user(mock_redis, mock_tg_user):
    """When Redis has no entry, create user via admin-api and cache."""
    mock_redis.get = AsyncMock(return_value=None)

    # Mock httpx responses
    user_response = MagicMock()
    user_response.status_code = 201
    user_response.json.return_value = {"id": 99, "email": "telegram:12345@telegram"}

    token_response = MagicMock()
    token_response.status_code = 201
    token_response.json.return_value = {"id": 1, "token": "tok_new_token"}

    with patch("bot.get_redis", return_value=mock_redis):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            # First call = create user, second = create token
            mock_client.post = AsyncMock(side_effect=[user_response, token_response])
            mock_client_cls.return_value = mock_client

            from bot import get_or_create_auth
            user_id, token = await get_or_create_auth(mock_tg_user)

    assert user_id == "99"
    assert token == "tok_new_token"
    mock_redis.set.assert_awaited_once_with("telegram:12345", "99:tok_new_token", ex=86400)


@pytest.mark.asyncio
async def test_auth_existing_user_returns_200(mock_redis, mock_tg_user):
    """When admin-api returns 200 (user exists), still create token."""
    mock_redis.get = AsyncMock(return_value=None)

    user_response = MagicMock()
    user_response.status_code = 200  # User already exists
    user_response.json.return_value = {"id": 5, "email": "telegram:12345@telegram"}

    token_response = MagicMock()
    token_response.status_code = 201
    token_response.json.return_value = {"id": 2, "token": "tok_existing"}

    with patch("bot.get_redis", return_value=mock_redis):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=[user_response, token_response])
            mock_client_cls.return_value = mock_client

            from bot import get_or_create_auth
            user_id, token = await get_or_create_auth(mock_tg_user)

    assert user_id == "5"
    assert token == "tok_existing"


@pytest.mark.asyncio
async def test_auth_admin_api_failure_raises(mock_redis, mock_tg_user):
    """When admin-api returns error, raise RuntimeError."""
    mock_redis.get = AsyncMock(return_value=None)

    user_response = MagicMock()
    user_response.status_code = 500
    user_response.text = "Internal Server Error"

    with patch("bot.get_redis", return_value=mock_redis):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=user_response)
            mock_client_cls.return_value = mock_client

            from bot import get_or_create_auth
            with pytest.raises(RuntimeError, match="Failed to create user"):
                await get_or_create_auth(mock_tg_user)
