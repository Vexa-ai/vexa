"""Tests for agent_api.container_manager — Runtime API delegation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from agent_api.container_manager import ContainerManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_http(**overrides):
    """Build a mock httpx.AsyncClient."""
    client = AsyncMock()
    client.post = AsyncMock()
    client.get = AsyncMock()
    client.delete = AsyncMock()
    client.aclose = AsyncMock()
    for k, v in overrides.items():
        setattr(client, k, v)
    return client


def _response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# start_agent
# ---------------------------------------------------------------------------

class TestStartAgent:
    @pytest.mark.asyncio
    async def test_posts_to_containers(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090", api_key="")
        cm._http = _mock_http()
        cm._http.post.return_value = _response(201, {"name": "agent-abc"})

        name = await cm.start_agent("session-1")

        cm._http.post.assert_called_once_with(
            "/containers", json={"user_id": "session-1", "profile": "agent"},
        )
        assert name == "agent-abc"

    @pytest.mark.asyncio
    async def test_container_id_extracted(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        cm._http.post.return_value = _response(200, {"name": "agent-xyz123"})

        name = await cm.start_agent("s1", agent_config={"model": "opus"})
        assert name == "agent-xyz123"

    @pytest.mark.asyncio
    async def test_config_forwarded(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        cm._http.post.return_value = _response(201, {"name": "agent-c"})

        await cm.start_agent("s1", agent_config={"model": "opus"})
        call_json = cm._http.post.call_args[1]["json"]
        assert call_json["config"] == {"model": "opus"}

    @pytest.mark.asyncio
    async def test_callback_url_forwarded(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        cm._http.post.return_value = _response(201, {"name": "agent-d"})

        await cm.start_agent("s1", callback_url="http://me/done")
        call_json = cm._http.post.call_args[1]["json"]
        assert call_json["callback_url"] == "http://me/done"

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        cm._http.post.return_value = _response(500, text="Internal Server Error")

        with pytest.raises(RuntimeError, match="Runtime API failed"):
            await cm.start_agent("s1")


# ---------------------------------------------------------------------------
# stop_agent
# ---------------------------------------------------------------------------

class TestStopAgent:
    @pytest.mark.asyncio
    async def test_sends_delete(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        cm._http.delete.return_value = _response(204)

        await cm.stop_agent("agent-abc")
        cm._http.delete.assert_called_once_with("/containers/agent-abc")

    @pytest.mark.asyncio
    async def test_removes_from_cache(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        cm._http.post.return_value = _response(201, {"name": "agent-abc"})
        cm._http.delete.return_value = _response(204)

        await cm.start_agent("user-1")
        assert cm.get_container_name("user-1") is not None

        await cm.stop_agent("agent-abc")
        # Should be removed from internal cache after stop
        # start_agent stores under session_id key
        assert cm.get_container_name("user-1") is None


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    @pytest.mark.asyncio
    async def test_gets_correct_url(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        cm._http.get.return_value = _response(200, {"name": "agent-x", "status": "running"})

        status = await cm.get_status("agent-x")
        cm._http.get.assert_called_once_with("/containers/agent-x")
        assert status["status"] == "running"

    @pytest.mark.asyncio
    async def test_not_found(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        cm._http.get.return_value = _response(404)

        status = await cm.get_status("agent-gone")
        assert status["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_server_error_raises(self):
        cm = ContainerManager(runtime_api_url="http://rt:8090")
        cm._http = _mock_http()
        resp = _response(500, text="boom")
        cm._http.get.return_value = resp

        with pytest.raises(httpx.HTTPStatusError):
            await cm.get_status("agent-err")
