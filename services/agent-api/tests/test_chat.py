"""Tests for agent_api.chat — session helpers and chat turn logic."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent_api.chat import (
    get_session,
    save_session,
    clear_session,
    list_sessions,
    save_session_meta,
    delete_session_meta,
    run_chat_turn,
    SESSION_PREFIX,
    SESSIONS_INDEX,
)


# ---------------------------------------------------------------------------
# Redis mock helper
# ---------------------------------------------------------------------------

def _mock_redis(**kv):
    """Build a mock async Redis client with optional initial values."""
    store = dict(kv)
    r = AsyncMock()
    r.get = AsyncMock(side_effect=lambda k: store.get(k))
    r.set = AsyncMock(side_effect=lambda k, v, **kw: store.__setitem__(k, v))
    r.delete = AsyncMock(side_effect=lambda k: store.pop(k, None))
    r.hgetall = AsyncMock(return_value={})
    r.hget = AsyncMock(return_value=None)
    r.hset = AsyncMock()
    r.hdel = AsyncMock()
    r.expire = AsyncMock()
    return r


# ---------------------------------------------------------------------------
# Session helpers — get / save / clear
# ---------------------------------------------------------------------------

class TestGetSession:
    @pytest.mark.asyncio
    async def test_explicit_session_id_returned(self):
        redis = _mock_redis()
        result = await get_session(redis, "user-1", session_id="explicit-id")
        assert result == "explicit-id"
        redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_redis(self):
        redis = _mock_redis(**{f"{SESSION_PREFIX}user-1": "redis-session"})
        result = await get_session(redis, "user-1")
        assert result == "redis-session"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_session(self):
        redis = _mock_redis()
        result = await get_session(redis, "user-1")
        assert result is None


class TestSaveSession:
    @pytest.mark.asyncio
    async def test_saves_with_ttl(self):
        redis = _mock_redis()
        await save_session(redis, "user-1", "sess-abc")
        redis.set.assert_called_once_with(
            f"{SESSION_PREFIX}user-1", "sess-abc", ex=86400 * 7,
        )


class TestClearSession:
    @pytest.mark.asyncio
    async def test_deletes_key(self):
        redis = _mock_redis()
        await clear_session(redis, "user-1")
        redis.delete.assert_called_once_with(f"{SESSION_PREFIX}user-1")


# ---------------------------------------------------------------------------
# Session index — list / save_meta / delete_meta
# ---------------------------------------------------------------------------

class TestListSessions:
    @pytest.mark.asyncio
    async def test_empty_index(self):
        redis = _mock_redis()
        redis.hgetall.return_value = {}
        result = await list_sessions(redis, "user-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_parsed_sessions(self):
        meta = json.dumps({"name": "My Session", "updated_at": 1000})
        redis = _mock_redis()
        redis.hgetall.return_value = {"sess-1": meta}
        result = await list_sessions(redis, "user-1")
        assert len(result) == 1
        assert result[0]["id"] == "sess-1"
        assert result[0]["name"] == "My Session"

    @pytest.mark.asyncio
    async def test_handles_bad_json(self):
        redis = _mock_redis()
        redis.hgetall.return_value = {"sess-bad": "not-json"}
        result = await list_sessions(redis, "user-1")
        assert len(result) == 1
        assert result[0]["id"] == "sess-bad"

    @pytest.mark.asyncio
    async def test_sessions_sorted_by_updated_at(self):
        redis = _mock_redis()
        redis.hgetall.return_value = {
            "old": json.dumps({"name": "Old", "updated_at": 100}),
            "new": json.dumps({"name": "New", "updated_at": 999}),
        }
        result = await list_sessions(redis, "user-1")
        assert result[0]["id"] == "new"
        assert result[1]["id"] == "old"


class TestSaveSessionMeta:
    @pytest.mark.asyncio
    async def test_creates_new_meta(self):
        redis = _mock_redis()
        redis.hget.return_value = None
        await save_session_meta(redis, "user-1", "sess-1", "My Session")
        redis.hset.assert_called_once()
        key, field, value = redis.hset.call_args[0]
        assert key == f"{SESSIONS_INDEX}user-1"
        assert field == "sess-1"
        meta = json.loads(value)
        assert meta["name"] == "My Session"
        assert "created_at" in meta
        assert "updated_at" in meta

    @pytest.mark.asyncio
    async def test_updates_existing_meta(self):
        existing = json.dumps({"name": "Old", "created_at": 42})
        redis = _mock_redis()
        redis.hget.return_value = existing
        await save_session_meta(redis, "user-1", "sess-1", "Renamed")
        _, _, value = redis.hset.call_args[0]
        meta = json.loads(value)
        assert meta["name"] == "Renamed"
        assert meta["created_at"] == 42  # preserved


class TestDeleteSessionMeta:
    @pytest.mark.asyncio
    async def test_removes_field(self):
        redis = _mock_redis()
        await delete_session_meta(redis, "user-1", "sess-1")
        redis.hdel.assert_called_once_with(f"{SESSIONS_INDEX}user-1", "sess-1")


# ---------------------------------------------------------------------------
# run_chat_turn — core streaming logic
# ---------------------------------------------------------------------------

def _mock_cm(container="agent-user1", new_container=False, exec_simple_output=None):
    """Build a mock ContainerManager for chat turn tests."""
    from agent_api.container_manager import ContainerInfo
    cm = AsyncMock()
    cm._new_container = new_container
    cm._containers = {}
    cm.ensure_container = AsyncMock(side_effect=lambda uid, **kw: _set_new(cm, new_container, container))
    cm.get_user_data = AsyncMock(return_value={})
    cm.exec_simple = AsyncMock(return_value=exec_simple_output)
    cm.exec_with_stdin = AsyncMock()

    # Create a mock process with async iterator stdout
    proc = AsyncMock()
    result_line = json.dumps({
        "type": "result",
        "session_id": "new-sess-123",
        "cost_usd": 0.01,
        "duration_ms": 500,
    }).encode() + b"\n"
    proc.stdout.__aiter__ = lambda self: _async_iter([result_line])
    proc.wait = AsyncMock()
    cm.exec_stream = AsyncMock(return_value=proc)
    return cm


def _set_new(cm, new_container, container):
    cm._new_container = new_container
    return container


async def _async_iter(items):
    for item in items:
        yield item


class TestRunChatTurn:
    @pytest.mark.asyncio
    async def test_yields_stream_end(self):
        redis = _mock_redis()
        cm = _mock_cm()
        events = []
        async for data in run_chat_turn(redis, cm, "user-1", "Hello"):
            events.append(data)
        # Should have at least a stream_end event
        assert any('"stream_end"' in e for e in events)

    @pytest.mark.asyncio
    async def test_new_container_sends_session_reset(self):
        redis = _mock_redis()
        cm = _mock_cm(new_container=True)
        events = []
        async for data in run_chat_turn(redis, cm, "user-1", "Hello"):
            events.append(data)
        assert any('"session_reset"' in e for e in events)

    @pytest.mark.asyncio
    async def test_saves_session_to_redis(self):
        redis = _mock_redis()
        cm = _mock_cm()
        events = []
        async for data in run_chat_turn(redis, cm, "user-1", "Hello"):
            events.append(data)
        # The result event contains session_id "new-sess-123", so it should be saved
        redis.set.assert_called()

    @pytest.mark.asyncio
    async def test_resumes_existing_session(self):
        redis = _mock_redis(**{f"{SESSION_PREFIX}user-1": "existing-sess"})
        cm = _mock_cm(exec_simple_output="OK")
        events = []
        async for data in run_chat_turn(redis, cm, "user-1", "Hello"):
            events.append(data)
        # Should have called exec_simple to check session file
        cm.exec_simple.assert_called()

    @pytest.mark.asyncio
    async def test_missing_session_file_clears_session(self):
        redis = _mock_redis(**{f"{SESSION_PREFIX}user-1": "stale-sess"})
        cm = _mock_cm(exec_simple_output="MISSING")
        events = []
        async for data in run_chat_turn(redis, cm, "user-1", "Hello"):
            events.append(data)
        # Should have cleared the stale session
        redis.delete.assert_called_with(f"{SESSION_PREFIX}user-1")

    @pytest.mark.asyncio
    async def test_context_prefix_prepended(self):
        redis = _mock_redis()
        cm = _mock_cm()
        events = []
        async for data in run_chat_turn(
            redis, cm, "user-1", "Hello",
            context_prefix="You are a helpful assistant",
        ):
            events.append(data)
        # exec_with_stdin should have been called with the prompt
        cm.exec_with_stdin.assert_called_once()
