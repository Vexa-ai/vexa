"""A worker's delegation token is a bearer like any other — one MCP server, one auth path.

ADR-0037 §2: the gateway assembles ONE MCP server and refuses what it cannot authenticate. A
delegation token (`vxd_…`) is resolved by identity through the same Authorizer port as an API key
(`/internal/validate` verifies its signature, audience and expiry, and answers with the person plus
the dispatch's ceiling); the gateway then signs that identity onto the forward like every other.
There is no second upstream for it and no branch that skips authentication or the rate limiter.
"""
from fastapi.testclient import TestClient

from gateway import create_app, identity_token
from conftest import SIGNING_KEY, VERIFY_KEY, FakeAuthorizer, FakeDownstream, FakeRedis, VALID_KEY

DELEGATED = "vxd_header.payload.signature"
DELEGATED_USER = {"user_id": 42, "scopes": ["bot", "tx"], "max_concurrent": 3,
                  "email": "ada@example.com",
                  "delegation": {"regime": "autonomous", "workspaces": ["ws_1"], "target": "ws_1"}}


def _client(user=DELEGATED_USER, key=DELEGATED, rate_limiter=None):
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=user, valid_key=key), downstream, FakeRedis(),
                     identity_key=SIGNING_KEY, rate_limiter=rate_limiter)
    return TestClient(app), downstream


def test_a_delegation_bearer_reaches_the_one_assembled_mcp_with_a_signed_identity():
    client, downstream = _client()
    r = client.post("/mcp", headers={"Authorization": f"Bearer {DELEGATED}",
                                     "X-User-Id": "victim", "X-Admin-API-Key": "injected"}, json={})
    assert r.status_code == 200
    fwd = downstream.last
    assert fwd["url"] == "http://mcp:8010/mcp"
    claims = identity_token.verify(VERIFY_KEY, fwd["headers"][identity_token.HEADER])
    assert claims["sub"] == "42"
    assert claims["delegation"] == {"regime": "autonomous", "workspaces": ["ws_1"], "target": "ws_1"}
    assert fwd["headers"]["x-user-id"] == "42"
    assert fwd["headers"]["x-user-regime"] == "autonomous"
    assert "x-admin-api-key" not in fwd["headers"]


def test_an_unknown_delegation_bearer_is_refused_before_any_forward():
    client, downstream = _client()
    r = client.post("/mcp", headers={"Authorization": "Bearer vxd_forged.x.y"}, json={})
    assert r.status_code == 401
    assert downstream.last is None


def test_a_delegation_bearer_is_rate_limited_like_any_caller():
    class Deny:
        def allow(self, key):
            return False

    client, downstream = _client(rate_limiter=Deny())
    r = client.post("/mcp", headers={"Authorization": f"Bearer {DELEGATED}"}, json={})
    assert r.status_code == 429
    assert downstream.last is None


def test_the_tool_calls_behind_the_mcp_resolve_the_same_bearer_on_rest_routes():
    """The assembled MCP forwards the caller's bearer to the routes its tools bind; the edge
    resolves it there exactly as on /mcp, so a worker's tools act as the person it acts for."""
    client, downstream = _client()
    r = client.get("/meetings", headers={"X-API-Key": DELEGATED})
    assert r.status_code == 200
    assert identity_token.verify(VERIFY_KEY, downstream.last["headers"][identity_token.HEADER])["sub"] == "42"


def test_api_key_mcp_traffic_reaches_the_same_server():
    client, downstream = _client(user={"user_id": 7, "scopes": ["bot", "tx"], "max_concurrent": 3},
                                 key=VALID_KEY)
    assert client.post("/mcp", headers={"X-API-Key": VALID_KEY}, json={}).status_code == 200
    assert downstream.last["url"] == "http://mcp:8010/mcp"


def test_create_app_has_no_second_mcp_upstream():
    import inspect

    assert "agent_mcp_url" not in inspect.signature(create_app).parameters
