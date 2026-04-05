"""Tests for meeting command handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot import ChatState


def _patch_auth(user_id="42", token="tok_test"):
    return patch("bot.get_or_create_auth", AsyncMock(return_value=(user_id, token)))


def _clear_states():
    """Clear bot state between tests."""
    from bot import _states
    _states.clear()


def _setup_state_with_meeting(chat_id=67890, user_id="42", meeting="google_meet/abc-def"):
    """Pre-populate state with an active meeting."""
    from bot import _states, ChatState
    state = ChatState(user_id=user_id, tg_user_id=12345, token="tok_test", active_meeting=meeting)
    _states[(chat_id, user_id)] = state
    return state


@pytest.mark.asyncio
async def test_join_command_no_url(mock_update, mock_context):
    """Test /join without URL shows usage."""
    mock_context.args = []

    with _patch_auth():
        from bot import join_command
        await join_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "Usage" in call_text


@pytest.mark.asyncio
async def test_join_command_success(mock_update, mock_context):
    """Test /join with valid URL creates bot."""
    mock_context.args = ["https://meet.google.com/abc-defg-hij"]

    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {
        "platform": "google_meet",
        "native_meeting_id": "abc-defg-hij",
    }

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import join_command, _states
            await join_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "joining" in call_text.lower()
    assert "google_meet" in call_text

    # Verify active_meeting was set
    state = _states.get((67890, "42"))
    assert state is not None
    assert state.active_meeting == "google_meet/abc-defg-hij"


@pytest.mark.asyncio
async def test_stop_no_meeting(mock_update, mock_context):
    """Test /stop without active meeting."""
    _clear_states()
    with _patch_auth():
        from bot import stop_meeting_command
        await stop_meeting_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "No active meeting" in call_text


@pytest.mark.asyncio
async def test_stop_with_meeting(mock_update, mock_context):
    """Test /stop with active meeting."""
    _setup_state_with_meeting()

    resp = MagicMock()
    resp.status_code = 200

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.delete = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import stop_meeting_command, _states
            await stop_meeting_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "stopped" in call_text.lower()
    assert _states[(67890, "42")].active_meeting is None


@pytest.mark.asyncio
async def test_speak_no_meeting(mock_update, mock_context):
    """Test /speak without active meeting."""
    mock_context.args = ["Hello", "world"]

    with _patch_auth():
        from bot import speak_command
        await speak_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "No active meeting" in call_text


@pytest.mark.asyncio
async def test_speak_no_text(mock_update, mock_context):
    """Test /speak without text."""
    _setup_state_with_meeting()
    mock_context.args = []

    with _patch_auth():
        from bot import speak_command
        await speak_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "Usage" in call_text


@pytest.mark.asyncio
async def test_speak_success(mock_update, mock_context):
    """Test /speak with text and active meeting."""
    _setup_state_with_meeting()
    mock_context.args = ["Hello", "meeting"]

    resp = MagicMock()
    resp.status_code = 200

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import speak_command
            await speak_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "Speaking" in call_text


@pytest.mark.asyncio
async def test_transcript_no_meeting(mock_update, mock_context):
    """Test /transcript without active meeting."""
    _clear_states()
    with _patch_auth():
        from bot import transcript_command
        await transcript_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "No active meeting" in call_text


@pytest.mark.asyncio
async def test_transcript_success(mock_update, mock_context):
    """Test /transcript with data."""
    _setup_state_with_meeting()

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "segments": [
            {"speaker": "Alice", "text": "Hello everyone"},
            {"speaker": "Bob", "text": "Hi Alice"},
        ]
    }

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import transcript_command
            await transcript_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "Alice" in call_text
    assert "Bob" in call_text
