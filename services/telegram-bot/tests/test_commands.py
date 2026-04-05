"""Tests for command handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _patch_auth(user_id="42", token="tok_test"):
    """Patch get_or_create_auth to return fixed values."""
    return patch("bot.get_or_create_auth", AsyncMock(return_value=(user_id, token)))


@pytest.mark.asyncio
async def test_start_command(mock_update, mock_context):
    """Test /start sends welcome message with user ID."""
    with _patch_auth():
        from bot import start_command
        await start_command(mock_update, mock_context)

    mock_update.message.reply_text.assert_awaited_once()
    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "42" in call_text
    assert "Vexa Agent ready" in call_text
    assert "/new" in call_text


@pytest.mark.asyncio
async def test_help_command(mock_update, mock_context):
    """Test /help shows all commands."""
    from bot import help_command
    await help_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "/join" in call_text
    assert "/stop" in call_text
    assert "/speak" in call_text
    assert "/transcript" in call_text
    assert "/files" in call_text
    assert "/sessions" in call_text


@pytest.mark.asyncio
async def test_new_session_command(mock_update, mock_context):
    """Test /new creates a session via agent-api."""
    mock_context.args = ["My", "Session"]

    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"session_id": "sess-123", "name": "My Session"}

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import new_session_command
            await new_session_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "My Session" in call_text
    assert "sess-123" in call_text


@pytest.mark.asyncio
async def test_sessions_command_empty(mock_update, mock_context):
    """Test /sessions with no sessions."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"sessions": []}

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import sessions_command
            await sessions_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "No sessions found" in call_text


@pytest.mark.asyncio
async def test_sessions_command_with_data(mock_update, mock_context):
    """Test /sessions lists sessions."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"sessions": [
        {"session_id": "abc-12345678", "name": "First"},
        {"session_id": "def-87654321", "name": "Second"},
    ]}

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import sessions_command
            await sessions_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "First" in call_text
    assert "Second" in call_text


@pytest.mark.asyncio
async def test_files_command_empty(mock_update, mock_context):
    """Test /files with empty workspace."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"files": []}

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import files_command
            await files_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "empty" in call_text.lower()


@pytest.mark.asyncio
async def test_files_command_with_files(mock_update, mock_context):
    """Test /files with workspace files."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"files": ["main.py", "utils.py", "README.md"]}

    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=resp)
            mock_client_cls.return_value = mock_client

            from bot import files_command
            await files_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "main.py" in call_text
    assert "utils.py" in call_text


@pytest.mark.asyncio
async def test_reset_command(mock_update, mock_context):
    """Test /reset resets session."""
    with _patch_auth():
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
            mock_client.request = AsyncMock(return_value=MagicMock(status_code=200))
            mock_client_cls.return_value = mock_client

            from bot import reset_command
            await reset_command(mock_update, mock_context)

    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "reset" in call_text.lower()
