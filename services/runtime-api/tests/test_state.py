"""Tests for Redis state management."""

import json
import time

import pytest

from runtime_api import state


class FakeRedis:
    """Minimal async Redis mock for unit tests."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ex=None):
        self._store[key] = value

    async def delete(self, key):
        self._store.pop(key, None)

    async def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        for key in list(self._store.keys()):
            if key.startswith(prefix):
                yield key


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.mark.asyncio
async def test_set_and_get_container(redis):
    """Container registered → retrievable by name."""
    data = {
        "status": "running",
        "profile": "worker",
        "user_id": "user-1",
        "container_id": "abc123",
        "created_at": time.time(),
    }
    await state.set_container(redis, "my-container", data)

    result = await state.get_container(redis, "my-container")
    assert result is not None
    assert result["status"] == "running"
    assert result["profile"] == "worker"
    assert result["user_id"] == "user-1"
    assert result["container_id"] == "abc123"
    # set_container should add updated_at
    assert "updated_at" in result


@pytest.mark.asyncio
async def test_get_nonexistent_container(redis):
    """Getting a nonexistent container returns None."""
    result = await state.get_container(redis, "does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_list_containers_returns_all(redis):
    """list_containers returns all active containers."""
    for i in range(3):
        data = {
            "status": "running",
            "profile": "worker",
            "user_id": f"user-{i}",
            "created_at": time.time(),
        }
        await state.set_container(redis, f"container-{i}", data)

    results = await state.list_containers(redis)
    assert len(results) == 3
    names = {c["name"] for c in results}
    assert names == {"container-0", "container-1", "container-2"}


@pytest.mark.asyncio
async def test_list_containers_filter_by_user(redis):
    """list_containers filters by user_id."""
    await state.set_container(redis, "c-a", {
        "status": "running", "profile": "worker", "user_id": "alice",
    })
    await state.set_container(redis, "c-b", {
        "status": "running", "profile": "worker", "user_id": "bob",
    })

    results = await state.list_containers(redis, user_id="alice")
    assert len(results) == 1
    assert results[0]["user_id"] == "alice"


@pytest.mark.asyncio
async def test_list_containers_filter_by_profile(redis):
    """list_containers filters by profile."""
    await state.set_container(redis, "c-w", {
        "status": "running", "profile": "worker", "user_id": "user-1",
    })
    await state.set_container(redis, "c-s", {
        "status": "running", "profile": "sandbox", "user_id": "user-1",
    })

    results = await state.list_containers(redis, profile="sandbox")
    assert len(results) == 1
    assert results[0]["profile"] == "sandbox"


@pytest.mark.asyncio
async def test_delete_container(redis):
    """Container removed → not in list."""
    await state.set_container(redis, "to-delete", {
        "status": "running", "profile": "worker", "user_id": "user-1",
    })

    await state.delete_container(redis, "to-delete")

    result = await state.get_container(redis, "to-delete")
    assert result is None

    results = await state.list_containers(redis)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_set_stopped(redis):
    """set_stopped marks container as stopped with TTL."""
    await state.set_container(redis, "stopping", {
        "status": "running", "profile": "worker", "user_id": "user-1",
    })

    await state.set_stopped(redis, "stopping")

    data = await state.get_container(redis, "stopping")
    assert data["status"] == "stopped"
    assert "stopped_at" in data


@pytest.mark.asyncio
async def test_set_stopped_with_exit_code(redis):
    """set_stopped records exit code when provided."""
    await state.set_container(redis, "failed-container", {
        "status": "running", "profile": "worker", "user_id": "user-1",
    })

    await state.set_stopped(redis, "failed-container", status="failed", exit_code=137)

    data = await state.get_container(redis, "failed-container")
    assert data["status"] == "failed"
    assert data["exit_code"] == 137


@pytest.mark.asyncio
async def test_count_user_containers(redis):
    """count_user_containers counts only running containers for a user."""
    await state.set_container(redis, "c-1", {
        "status": "running", "profile": "worker", "user_id": "alice",
    })
    await state.set_container(redis, "c-2", {
        "status": "running", "profile": "worker", "user_id": "alice",
    })
    await state.set_container(redis, "c-3", {
        "status": "stopped", "profile": "worker", "user_id": "alice",
    })
    await state.set_container(redis, "c-4", {
        "status": "running", "profile": "worker", "user_id": "bob",
    })

    count = await state.count_user_containers(redis, "alice")
    assert count == 2

    count_bob = await state.count_user_containers(redis, "bob")
    assert count_bob == 1


@pytest.mark.asyncio
async def test_count_user_containers_by_profile(redis):
    """count_user_containers filters by profile when specified."""
    await state.set_container(redis, "c-w", {
        "status": "running", "profile": "worker", "user_id": "alice",
    })
    await state.set_container(redis, "c-s", {
        "status": "running", "profile": "sandbox", "user_id": "alice",
    })

    count = await state.count_user_containers(redis, "alice", profile="worker")
    assert count == 1


@pytest.mark.asyncio
async def test_state_persists_across_redis_reconnect(redis):
    """Data persisted in Redis survives 'reconnect' (same backing store)."""
    await state.set_container(redis, "persistent", {
        "status": "running", "profile": "worker", "user_id": "user-1",
        "important_data": "must_survive",
    })

    # Simulate reconnect — create new FakeRedis sharing the same store
    redis2 = FakeRedis()
    redis2._store = redis._store  # same backing dict

    result = await state.get_container(redis2, "persistent")
    assert result is not None
    assert result["important_data"] == "must_survive"


@pytest.mark.asyncio
async def test_pending_callback_crud(redis):
    """Pending callbacks can be stored, retrieved, and deleted."""
    cb_data = {
        "url": "http://example.com/hook",
        "payload": {"name": "test", "status": "stopped"},
        "attempts": 0,
    }
    await state.store_pending_callback(redis, "test-container", cb_data)

    result = await state.get_pending_callback(redis, "test-container")
    assert result is not None
    assert result["url"] == "http://example.com/hook"

    await state.delete_pending_callback(redis, "test-container")

    result = await state.get_pending_callback(redis, "test-container")
    assert result is None


@pytest.mark.asyncio
async def test_set_container_updates_timestamp(redis):
    """set_container always updates updated_at."""
    t_before = time.time()
    await state.set_container(redis, "ts-test", {
        "status": "running", "profile": "worker", "user_id": "user-1",
    })
    t_after = time.time()

    data = await state.get_container(redis, "ts-test")
    assert t_before <= data["updated_at"] <= t_after
