"""Integration tests for recording proxy routes.

Verifies that /recordings/* routes correctly proxy to MEETING_API_URL.
"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from main import app


@pytest.fixture
def mock_response():
    """Create a mock httpx.Response."""
    def _make(status_code=200, json_body=None):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.content = __import__("json").dumps(json_body or {}).encode()
        resp.headers = {"content-type": "application/json"}
        return resp
    return _make


@pytest.fixture
def mock_http_client(mock_response):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response(200, {}))
    return client


@pytest.mark.asyncio
class TestListRecordings:
    async def test_list_recordings_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"recordings": []}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/recordings", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        call_args = mock_http_client.request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1].endswith("/recordings")

    async def test_list_recordings_forwards_query_params(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"recordings": []}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/recordings?meeting_id=42&limit=10", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        call_kwargs = mock_http_client.request.call_args[1]
        params = call_kwargs.get("params", {})
        assert params.get("meeting_id") == "42"
        assert params.get("limit") == "10"


@pytest.mark.asyncio
class TestGetRecording:
    async def test_get_recording_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"id": 5}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/recordings/5", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        assert "/recordings/5" in mock_http_client.request.call_args[0][1]

    async def test_get_recording_not_found(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(404, {"detail": "not found"}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/recordings/999", headers={"x-api-key": "k"})

        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDownloadMedia:
    async def test_download_media_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"url": "https://s3/file"}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/recordings/5/media/3/download", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        url = mock_http_client.request.call_args[0][1]
        assert "/recordings/5/media/3/download" in url

    async def test_download_media_raw_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/recordings/5/media/3/raw", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        url = mock_http_client.request.call_args[0][1]
        assert "/recordings/5/media/3/raw" in url


@pytest.mark.asyncio
class TestDeleteRecording:
    async def test_delete_recording_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"deleted": True}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.delete("/recordings/5", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        call_args = mock_http_client.request.call_args
        assert call_args[0][0] == "DELETE"
        assert "/recordings/5" in call_args[0][1]


@pytest.mark.asyncio
class TestRecordingConfig:
    async def test_get_recording_config_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"enabled": True}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/recording-config", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        assert mock_http_client.request.call_args[0][1].endswith("/recording-config")

    async def test_put_recording_config_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.put("/recording-config",
                                json={"enabled": False},
                                headers={"x-api-key": "k"})

        assert resp.status_code == 200
        call_args = mock_http_client.request.call_args
        assert call_args[0][0] == "PUT"


@pytest.mark.asyncio
class TestRecordingBackendErrors:
    async def test_backend_503(self, mock_http_client):
        mock_http_client.request = AsyncMock(side_effect=httpx.ConnectError("down"))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
            resp = await ac.get("/recordings", headers={"x-api-key": "k"})

        assert resp.status_code == 503
