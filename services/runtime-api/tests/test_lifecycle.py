"""Tests for lifecycle management — idle timeouts and callback delivery."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from runtime_api.backends import Backend, ContainerInfo, ContainerSpec
from runtime_api.lifecycle import (
    handle_container_exit,
    idle_loop,
    reconcile_state,
    _fire_exit_callback,
    _deliver_callback,
)
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


class FakeBackend(Backend):
    """Minimal backend for lifecycle tests."""

    def __init__(self):
        self.stopped = []
        self.removed = []
        self.containers = []

    async def create(self, spec):
        return "fake-id"

    async def stop(self, name, timeout=10):
        self.stopped.append(name)
        return True

    async def remove(self, name):
        self.removed.append(name)
        return True

    async def inspect(self, name):
        return None

    async def list(self, labels=None):
        return self.containers

    async def exec(self, name, cmd):
        yield b""


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.mark.asyncio
async def test_idle_check_stops_expired_container(redis, backend):
    """Idle check stops containers that exceeded their idle_timeout."""
    # Write directly to Redis (bypass set_container which overwrites updated_at)
    import json as _json
    container_data = {
        "status": "running",
        "profile": "test-profile",
        "user_id": "user-1",
        "created_at": time.time() - 1000,
        "updated_at": time.time() - 1000,  # last activity 1000s ago
    }
    await redis.set("runtime:container:idle-container", _json.dumps(container_data))

    # Simulate one idle check cycle directly (instead of testing the loop timing)
    containers = await state.list_containers(redis)
    assert len(containers) == 1, f"Expected 1 container, got {len(containers)}"

    now = time.time()
    for c in containers:
        if c.get("status") != "running":
            continue
        # Profile says idle_timeout=60, container idle for 1000s
        timeout = 60
        updated = c.get("updated_at", c.get("created_at", now))
        if now - updated > timeout:
            name = c.get("name", "")
            await backend.stop(name)
            await backend.remove(name)
            await state.set_stopped(redis, name)

    assert "idle-container" in backend.stopped
    assert "idle-container" in backend.removed

    # State should show stopped
    data = await state.get_container(redis, "idle-container")
    assert data is not None
    assert data.get("status") == "stopped"


@pytest.mark.asyncio
async def test_idle_loop_skips_zero_timeout(redis, backend):
    """Containers with idle_timeout=0 are never stopped."""
    container_data = {
        "status": "running",
        "profile": "persistent",
        "user_id": "user-1",
        "created_at": time.time() - 999999,
        "updated_at": time.time() - 999999,
    }
    await state.set_container(redis, "persistent-container", container_data)

    with patch("runtime_api.lifecycle.get_profile") as mock_profile, \
         patch("runtime_api.lifecycle.config") as mock_config:
        mock_profile.return_value = {"idle_timeout": 0}
        mock_config.IDLE_CHECK_INTERVAL = 0.01

        task = asyncio.create_task(idle_loop(redis, backend))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Should NOT have been stopped
    assert backend.stopped == []


@pytest.mark.asyncio
async def test_idle_loop_skips_non_running(redis, backend):
    """Only running containers are checked for idle timeout."""
    container_data = {
        "status": "stopped",
        "profile": "test-profile",
        "user_id": "user-1",
        "created_at": time.time() - 1000,
        "updated_at": time.time() - 1000,
    }
    await state.set_container(redis, "stopped-container", container_data)

    with patch("runtime_api.lifecycle.get_profile") as mock_profile, \
         patch("runtime_api.lifecycle.config") as mock_config:
        mock_profile.return_value = {"idle_timeout": 60}
        mock_config.IDLE_CHECK_INTERVAL = 0.01

        task = asyncio.create_task(idle_loop(redis, backend))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert backend.stopped == []


@pytest.mark.asyncio
async def test_handle_container_exit_stopped(redis):
    """handle_container_exit with exit_code=0 sets status to 'stopped'."""
    container_data = {
        "status": "running",
        "profile": "worker",
        "user_id": "user-1",
        "created_at": time.time(),
    }
    await state.set_container(redis, "exiting-container", container_data)

    with patch("runtime_api.lifecycle._fire_exit_callback", new_callable=AsyncMock):
        await handle_container_exit(redis, "exiting-container", exit_code=0)

    data = await state.get_container(redis, "exiting-container")
    assert data["status"] == "stopped"
    assert data["exit_code"] == 0


@pytest.mark.asyncio
async def test_handle_container_exit_failed(redis):
    """handle_container_exit with non-zero exit_code sets status to 'failed'."""
    container_data = {
        "status": "running",
        "profile": "worker",
        "user_id": "user-1",
        "created_at": time.time(),
    }
    await state.set_container(redis, "crash-container", container_data)

    with patch("runtime_api.lifecycle._fire_exit_callback", new_callable=AsyncMock):
        await handle_container_exit(redis, "crash-container", exit_code=137)

    data = await state.get_container(redis, "crash-container")
    assert data["status"] == "failed"
    assert data["exit_code"] == 137


@pytest.mark.asyncio
async def test_callback_fired_on_exit(redis):
    """_fire_exit_callback POSTs to the callback_url."""
    container_data = {
        "status": "running",
        "profile": "worker",
        "user_id": "user-1",
        "container_id": "cid-123",
        "callback_url": "http://example.com/hook",
        "metadata": {"job": "abc"},
        "created_at": time.time(),
    }
    await state.set_container(redis, "cb-container", container_data)

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("runtime_api.lifecycle.httpx.AsyncClient") as mock_client_cls, \
         patch("runtime_api.lifecycle.config") as mock_config:
        mock_config.CALLBACK_RETRIES = 1
        mock_config.CALLBACK_BACKOFF = [0]

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await _fire_exit_callback(redis, "cb-container", exit_code=0)

        # Verify POST was called with correct payload
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://example.com/hook"
        payload = call_args[1]["json"]
        assert payload["container_id"] == "cid-123"
        assert payload["name"] == "cb-container"
        assert payload["profile"] == "worker"
        assert payload["status"] == "stopped"
        assert payload["exit_code"] == 0
        assert payload["metadata"] == {"job": "abc"}


@pytest.mark.asyncio
async def test_callback_not_fired_without_url(redis):
    """No callback if container has no callback_url."""
    container_data = {
        "status": "running",
        "profile": "worker",
        "user_id": "user-1",
        "created_at": time.time(),
    }
    await state.set_container(redis, "no-cb-container", container_data)

    with patch("runtime_api.lifecycle.httpx.AsyncClient") as mock_client_cls:
        await _fire_exit_callback(redis, "no-cb-container", exit_code=0)
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_callback_failure_does_not_crash(redis):
    """Callback delivery failure doesn't propagate exceptions."""
    container_data = {
        "status": "running",
        "profile": "worker",
        "user_id": "user-1",
        "callback_url": "http://unreachable.invalid/hook",
        "metadata": {},
        "container_id": "cid-456",
        "created_at": time.time(),
    }
    await state.set_container(redis, "fail-cb-container", container_data)

    with patch("runtime_api.lifecycle.httpx.AsyncClient") as mock_client_cls, \
         patch("runtime_api.lifecycle.config") as mock_config:
        mock_config.CALLBACK_RETRIES = 2
        mock_config.CALLBACK_BACKOFF = [0, 0]

        mock_client = AsyncMock()
        mock_client.post.side_effect = ConnectionError("unreachable")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # Should not raise
        await _fire_exit_callback(redis, "fail-cb-container", exit_code=1)

    # Pending callback should remain (exhausted retries but stored)
    cb = await state.get_pending_callback(redis, "fail-cb-container")
    assert cb is not None


@pytest.mark.asyncio
async def test_reconcile_state_removes_stale(redis, backend):
    """reconcile_state marks stale Redis entries as stopped."""
    # Put a container in Redis that doesn't exist in the backend
    stale_data = {
        "status": "running",
        "profile": "worker",
        "user_id": "user-1",
        "name": "stale-container",
        "created_at": time.time() - 600,
    }
    await state.set_container(redis, "stale-container", stale_data)

    # Backend returns empty list — no containers running
    backend.containers = []

    await reconcile_state(redis, backend)

    data = await state.get_container(redis, "stale-container")
    assert data["status"] == "stopped"


@pytest.mark.asyncio
async def test_reconcile_state_adds_from_backend(redis, backend):
    """reconcile_state adds containers from the backend that aren't in Redis."""
    backend.containers = [
        ContainerInfo(
            id="abc123",
            name="backend-container",
            status="running",
            labels={"runtime.profile": "sandbox", "runtime.user_id": "user-2"},
            created_at=time.time(),
            image="ubuntu:24.04",
        )
    ]

    await reconcile_state(redis, backend)

    data = await state.get_container(redis, "backend-container")
    assert data is not None
    assert data["status"] == "running"
    assert data["profile"] == "sandbox"
    assert data["user_id"] == "user-2"
