"""Tests for token refresh, concurrent user isolation, and group chat handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _patch_auth(user_id="42", token="tok_test"):
    return patch("bot.get_or_create_auth", AsyncMock(return_value=(user_id, token)))


def _clear_states():
    from bot import _states
    _states.clear()


# --- Token refresh on 403 ---


@pytest.mark.asyncio
async def test_token_cache_has_ttl(mock_redis, mock_tg_user):
    """Redis SET uses TTL (86400s) — tokens expire after 24h."""
    mock_redis.get = AsyncMock(return_value=None)

    user_response = MagicMock()
    user_response.status_code = 201
    user_response.json.return_value = {"id": 99}

    token_response = MagicMock()
    token_response.status_code = 201
    token_response.json.return_value = {"token": "tok_new"}

    with patch("bot.get_redis", return_value=mock_redis):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=[user_response, token_response])
            mock_client_cls.return_value = mock_client

            from bot import get_or_create_auth
            await get_or_create_auth(mock_tg_user)

    # Verify TTL was set
    mock_redis.set.assert_awaited_once_with("telegram:12345", "99:tok_new", ex=86400)


@pytest.mark.asyncio
async def test_invalidate_token_clears_redis(mock_redis):
    """_invalidate_token removes the cached key from Redis."""
    with patch("bot.get_redis", return_value=mock_redis):
        from bot import _invalidate_token
        await _invalidate_token(12345)

    mock_redis.delete.assert_awaited_once_with("telegram:12345")


# --- Concurrent user isolation ---


def test_state_keyed_by_chat_and_user():
    """_get_state uses (chat_id, user_id) as key — two users in same chat get separate states."""
    _clear_states()
    from bot import _get_state, _states

    state_a = _get_state(chat_id=100, user_id="user_a", token="tok_a", tg_user_id=1)
    state_b = _get_state(chat_id=100, user_id="user_b", token="tok_b", tg_user_id=2)

    assert state_a is not state_b
    assert state_a.user_id == "user_a"
    assert state_b.user_id == "user_b"
    assert state_a.token == "tok_a"
    assert state_b.token == "tok_b"

    # Same user in same chat returns same state
    state_a2 = _get_state(chat_id=100, user_id="user_a")
    assert state_a2 is state_a


def test_state_isolated_across_chats():
    """Same user in different chats gets separate state."""
    _clear_states()
    from bot import _get_state

    state_1 = _get_state(chat_id=100, user_id="user_a", token="tok", tg_user_id=1)
    state_2 = _get_state(chat_id=200, user_id="user_a", token="tok", tg_user_id=1)

    assert state_1 is not state_2
    state_1.active_meeting = "google_meet/abc"
    assert state_2.active_meeting is None


def test_concurrent_users_no_crosstalk():
    """Two users' meetings don't interfere."""
    _clear_states()
    from bot import _get_state

    alice = _get_state(chat_id=100, user_id="alice", token="tok_a", tg_user_id=1)
    bob = _get_state(chat_id=100, user_id="bob", token="tok_b", tg_user_id=2)

    alice.active_meeting = "google_meet/meeting-1"
    alice.accumulated = "Alice's response"

    assert bob.active_meeting is None
    assert bob.accumulated == ""


def test_tg_user_id_stored_in_state():
    """ChatState stores tg_user_id for token refresh."""
    _clear_states()
    from bot import _get_state

    state = _get_state(chat_id=100, user_id="42", token="tok", tg_user_id=12345)
    assert state.tg_user_id == 12345


# --- Group chat handling ---


def test_is_group_chat_private():
    """Private chat is not a group chat."""
    from bot import _is_group_chat
    update = MagicMock()
    update.effective_chat.type = "private"
    assert _is_group_chat(update) is False


def test_is_group_chat_group():
    """Group chat detected."""
    from bot import _is_group_chat
    update = MagicMock()
    update.effective_chat.type = "group"
    assert _is_group_chat(update) is True


def test_is_group_chat_supergroup():
    """Supergroup detected as group chat."""
    from bot import _is_group_chat
    update = MagicMock()
    update.effective_chat.type = "supergroup"
    assert _is_group_chat(update) is True


def test_should_respond_in_group_reply_to_bot():
    """Bot responds when message is a reply to bot's message."""
    from bot import _should_respond_in_group
    update = MagicMock()
    context = MagicMock()
    context.bot.id = 999
    context.bot.username = "vexa_bot"
    update.message.reply_to_message.from_user.id = 999
    update.message.entities = None

    assert _should_respond_in_group(update, context) is True


def test_should_respond_in_group_mention():
    """Bot responds when @mentioned."""
    from bot import _should_respond_in_group
    update = MagicMock()
    context = MagicMock()
    context.bot.id = 999
    context.bot.username = "vexa_bot"
    update.message.reply_to_message = None

    entity = MagicMock()
    entity.type = "mention"
    entity.offset = 0
    entity.length = 9
    update.message.text = "@vexa_bot hello"
    update.message.entities = [entity]

    assert _should_respond_in_group(update, context) is True


def test_should_respond_in_group_no_trigger():
    """Bot ignores unrelated messages in group."""
    from bot import _should_respond_in_group
    update = MagicMock()
    context = MagicMock()
    context.bot.id = 999
    context.bot.username = "vexa_bot"
    update.message.reply_to_message = None
    update.message.entities = []

    assert _should_respond_in_group(update, context) is False


@pytest.mark.asyncio
async def test_handle_message_group_ignored_without_mention(mock_update, mock_context):
    """In group chat, messages without @mention are ignored."""
    mock_update.effective_chat.type = "group"
    mock_update.message.text = "random message"
    mock_update.message.reply_to_message = None
    mock_update.message.entities = []
    mock_context.bot.username = "vexa_bot"
    mock_context.bot.id = 999

    with patch("bot._start_stream", AsyncMock()) as mock_stream:
        from bot import handle_message
        await handle_message(mock_update, mock_context)
        mock_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_group_responds_to_mention(mock_update, mock_context):
    """In group chat, messages with @mention trigger response."""
    mock_update.effective_chat.type = "group"
    mock_update.message.text = "@vexa_bot what is the weather?"
    mock_update.message.reply_to_message = None

    entity = MagicMock()
    entity.type = "mention"
    entity.offset = 0
    entity.length = 9
    mock_update.message.entities = [entity]

    mock_context.bot.username = "vexa_bot"
    mock_context.bot.id = 999

    with _patch_auth():
        with patch("bot._start_stream", AsyncMock()) as mock_stream:
            from bot import handle_message
            await handle_message(mock_update, mock_context)
            mock_stream.assert_awaited_once()
            # Verify @mention was stripped from message
            assert mock_stream.call_args[0][3] == "what is the weather?"


@pytest.mark.asyncio
async def test_handle_message_private_always_responds(mock_update, mock_context):
    """In private chat, all messages get responses."""
    mock_update.effective_chat.type = "private"
    mock_update.message.text = "Hello agent"
    mock_update.message.entities = []

    with _patch_auth():
        with patch("bot._start_stream", AsyncMock()) as mock_stream:
            from bot import handle_message
            await handle_message(mock_update, mock_context)
            mock_stream.assert_awaited_once()
