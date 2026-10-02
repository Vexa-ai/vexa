"""Config discovery must complete every page before the sweep sees any configs."""
import logging

import httpx
import pytest

from meeting_api.calendar_sync.adapters import fetch_configs


@pytest.mark.asyncio
@pytest.mark.parametrize("second", ["ok", "http", "json", "shape", "loop", "cursor", "network", "missing"])
async def test_config_pages(monkeypatch, caplog, second):
    requests = []
    client_type = httpx.AsyncClient

    def serve(request):
        requests.append(request)
        assert request.headers["X-Internal-Secret"] == "private-test-secret"
        assert request.url.params["limit"] == "200"
        if len(requests) == 1:
            return httpx.Response(200, json={"configs": [{"user_id": 1}], "next_cursor": "page2"})
        assert request.url.params["cursor"] == "page2"
        if second == "http":
            return httpx.Response(503)
        if second == "json":
            return httpx.Response(200, text="invalid")
        if second == "shape":
            return httpx.Response(200, json={"configs": {}})
        if second == "network":
            raise httpx.ConnectError("unavailable")
        if second == "missing":
            return httpx.Response(200, json={"configs": [{"user_id": 2}]})
        return httpx.Response(200, json={"configs": [{"user_id": 2}], "next_cursor":
                                       "page2" if second == "loop" else 7 if second == "cursor" else None})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(serve), **kw))
    with caplog.at_level(logging.INFO):
        result = await fetch_configs(admin_api_url="http://identity", internal_secret="private-test-secret")
    assert result == ([{"user_id": 1}, {"user_id": 2}] if second == "ok" else None)
    assert len(requests) == 2
    if second == "ok":
        assert "pages=2" in caplog.text
    assert "private-test-secret" not in caplog.text
