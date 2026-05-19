"""Agent chat proxy auth tests."""

import httpx
import pytest
from httpx import ASGITransport
from unittest.mock import AsyncMock, patch

from main import app


@pytest.mark.asyncio
async def test_agent_chat_requires_api_key_before_forwarding():
    client = AsyncMock(spec=httpx.AsyncClient)
    app.state.http_client = client

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/chat", json={"user_id": "1", "message": "hello"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing API key"
    client.request.assert_not_called()


@pytest.mark.asyncio
async def test_agent_chat_rejects_invalid_api_key_before_forwarding():
    client = AsyncMock(spec=httpx.AsyncClient)
    app.state.http_client = client

    with patch("main._resolve_token", AsyncMock(return_value=None)):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/chat",
                json={"user_id": "1", "message": "hello"},
                headers={"x-api-key": "bad-key"},
            )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"
    client.request.assert_not_called()

