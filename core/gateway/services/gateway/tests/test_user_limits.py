"""One limits encoder for REST forwarding and subscribe authorization, including old identity."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import create_app, user_limits
from gateway.adapters import AdminApiAuthorizer
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis


@pytest.mark.parametrize("extra", [{}, {"ramp_bots": 2}, {"ramp_window_s": 300}])
def test_header_without_both_ramp_fields_keeps_bare_ceiling(extra):
    assert user_limits.user_limits_header({"max_concurrent": 0, **extra}) == "0"
    assert user_limits.user_limits_header(extra) == "3"


@pytest.mark.parametrize("bots", [0, 2])
def test_header_with_ramp_is_json(bots):
    data = {"max_concurrent": 5, "ramp_bots": bots, "ramp_window_s": 300}
    assert json.loads(user_limits.user_limits_header(data)) == data


async def test_both_forwarding_paths_call_the_shared_helper(monkeypatch):
    data = {"user_id": 7, "scopes": ["bot"], "max_concurrent": 5,
            "ramp_bots": 2, "ramp_window_s": 300}
    calls = []

    def encode(value):
        calls.append(value)
        return "shared-limits-sentinel"

    monkeypatch.setattr(user_limits, "user_limits_header", encode)
    downstream = FakeDownstream()
    client = TestClient(create_app(FakeAuthorizer(user=data), downstream, FakeRedis()))
    assert client.post("/bots", headers={"x-api-key": VALID_KEY}, json={}).status_code == 200
    assert downstream.last["headers"]["x-user-limits"] == "shared-limits-sentinel"

    def handle(request):
        if request.url.path == "/internal/validate":
            return httpx.Response(200, json=data)
        assert request.url.path == "/ws/authorize-subscribe"
        assert request.headers["x-user-limits"] == "shared-limits-sentinel"
        return httpx.Response(200, json={"authorized": [], "errors": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        authorizer = AdminApiAuthorizer(transport, "http://admin", "http://meetings")
        assert await authorizer.authorize_subscribe(VALID_KEY, []) == {"authorized": [], "errors": []}
    assert calls == [data, data]
