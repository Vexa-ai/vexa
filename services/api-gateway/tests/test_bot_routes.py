"""Integration tests for bot management proxy routes.

Verifies that /bots/* routes correctly proxy to MEETING_API_URL,
forward headers (especially X-API-Key), and handle backend errors.
"""
import pytest
import httpx
from httpx import ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from main import app, forward_request


@pytest.fixture
def mock_response():
    """Create a mock httpx.Response."""
    def _make(status_code=200, json_body=None, content=b"ok"):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.content = content if json_body is None else __import__("json").dumps(json_body).encode()
        resp.headers = {"content-type": "application/json"}
        return resp
    return _make


@pytest.fixture
def mock_http_client(mock_response):
    """Patch app.state.http_client with a mock that returns 200 by default."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response(200, {"status": "ok"}))
    return client


@pytest.mark.asyncio
class TestPostBots:
    async def test_post_bots_proxies_to_meeting_api(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(201, {"id": 1}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/bots", json={"platform": "teams", "native_meeting_id": "abc123"},
                                 headers={"x-api-key": "test-key"})

        assert resp.status_code == 201
        call_args = mock_http_client.request.call_args
        assert call_args[0][0] == "POST"  # method
        assert "/bots" in call_args[0][1]  # url

    async def test_post_bots_forwards_api_key(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(201, {"id": 1}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.post("/bots", json={}, headers={"x-api-key": "my-secret-key"})

        call_args = mock_http_client.request.call_args
        forwarded_headers = call_args[1].get("headers", call_args[0][2] if len(call_args[0]) > 2 else {})
        assert forwarded_headers.get("x-api-key") == "my-secret-key"


@pytest.mark.asyncio
class TestGetBotsStatus:
    async def test_get_bots_status_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"bots": []}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/bots/status", headers={"x-api-key": "test-key"})

        assert resp.status_code == 200
        call_args = mock_http_client.request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1].endswith("/bots/status")


@pytest.mark.asyncio
class TestDeleteBot:
    async def test_delete_bot_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"status": "stopped"}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.delete("/bots/teams/meeting123", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        call_args = mock_http_client.request.call_args
        assert call_args[0][0] == "DELETE"
        assert "/bots/teams/meeting123" in call_args[0][1]


@pytest.mark.asyncio
class TestPutBotConfig:
    async def test_put_bot_config_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(202, {"accepted": True}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.put("/bots/teams/meeting123/config",
                                json={"language": "en"},
                                headers={"x-api-key": "k"})

        assert resp.status_code == 202
        call_args = mock_http_client.request.call_args
        assert call_args[0][0] == "PUT"
        assert "/config" in call_args[0][1]


@pytest.mark.asyncio
class TestSpeakRoute:
    async def test_post_speak_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"queued": True}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/bots/teams/m1/speak",
                                 json={"text": "Hello"},
                                 headers={"x-api-key": "k"})

        assert resp.status_code == 200
        assert "speak" in mock_http_client.request.call_args[0][1]


@pytest.mark.asyncio
class TestChatRoute:
    async def test_post_chat_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/bots/teams/m1/chat",
                                 json={"message": "hi"},
                                 headers={"x-api-key": "k"})

        assert resp.status_code == 200
        assert "/chat" in mock_http_client.request.call_args[0][1]

    async def test_get_chat_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {"messages": []}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/bots/teams/m1/chat", headers={"x-api-key": "k"})

        assert resp.status_code == 200
        assert mock_http_client.request.call_args[0][0] == "GET"


@pytest.mark.asyncio
class TestScreenRoute:
    async def test_post_screen_proxies(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/bots/teams/m1/screen",
                                 json={"url": "https://example.com"},
                                 headers={"x-api-key": "k"})

        assert resp.status_code == 200
        assert "/screen" in mock_http_client.request.call_args[0][1]


@pytest.mark.asyncio
class TestBackendErrors:
    async def test_backend_502_returns_error(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(502, {"detail": "bad gateway"}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/bots/status", headers={"x-api-key": "k"})

        assert resp.status_code == 502

    async def test_backend_connection_error_returns_503(self, mock_http_client):
        mock_http_client.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/bots", json={}, headers={"x-api-key": "k"})

        assert resp.status_code == 503

    async def test_backend_timeout_returns_503(self, mock_http_client):
        mock_http_client.request = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/bots", json={}, headers={"x-api-key": "k"})

        assert resp.status_code == 503


@pytest.mark.asyncio
class TestHeaderForwarding:
    async def test_api_key_forwarded_to_backend(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get("/bots/status", headers={"x-api-key": "secret-token-123"})

        call_kwargs = mock_http_client.request.call_args
        headers = call_kwargs[1].get("headers", {})
        assert headers.get("x-api-key") == "secret-token-123"

    async def test_host_header_not_forwarded(self, mock_http_client, mock_response):
        mock_http_client.request = AsyncMock(return_value=mock_response(200, {}))
        app.state.http_client = mock_http_client

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get("/bots/status", headers={"x-api-key": "k", "host": "evil.com"})

        call_kwargs = mock_http_client.request.call_args
        headers = call_kwargs[1].get("headers", {})
        # host should be excluded from forwarded headers
        assert "host" not in headers
