"""Shared fixtures for telegram-bot tests."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure bot module can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set required env vars before importing bot
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-123")
os.environ.setdefault("AGENT_API_URL", "http://localhost:8100")
os.environ.setdefault("ADMIN_API_URL", "http://localhost:8001")
os.environ.setdefault("ADMIN_API_TOKEN", "test-admin-token")
os.environ.setdefault("GATEWAY_URL", "http://localhost:8000")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock()
    redis_mock.ping = AsyncMock()
    return redis_mock


@pytest.fixture
def mock_tg_user():
    """Mock Telegram user."""
    user = MagicMock()
    user.id = 12345
    user.full_name = "Test User"
    user.username = "testuser"
    return user


@pytest.fixture
def mock_update(mock_tg_user):
    """Mock Telegram Update."""
    update = MagicMock()
    update.effective_user = mock_tg_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = 67890
    update.effective_chat.type = "private"
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.text = "Hello agent"
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    return update


@pytest.fixture
def mock_context():
    """Mock Telegram context."""
    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    ctx.bot.edit_message_text = AsyncMock()
    ctx.bot.send_chat_action = AsyncMock()
    ctx.args = []
    return ctx
