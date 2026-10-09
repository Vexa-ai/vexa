"""A worker's delegation token is an MCP credential — one MCP server, one auth path.

ADR-0037 §2: the gateway assembles ONE MCP server and refuses what it cannot authenticate. A
delegation token (`vxd_…`) is resolved by identity through the same Authorizer port as an API key
(`/internal/validate` verifies its signature, audience and expiry, and answers with the person plus
the dispatch's ceiling); the gateway then signs that identity onto the forward like every other.
There is no second upstream for it and no branch that skips authentication or the rate limiter.

It is ADMITTED on `/mcp` only. The MCP's tools call back into the edge with the same bearer and the
identity the edge signed onto the `/mcp` hop (`X-Vexa-Internal-Mcp-Identity`); that re-entry is
admitted, and nothing else a worker sends with its token is.
"""
from fastapi.testclient import TestClient

from gateway import create_app, identity_token
from conftest import (AGENT_CARRIED, SIGNING_KEY, VERIFY_KEY, FakeAuthorizer, FakeDownstream,
                      FakeRedis, VALID_KEY)

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


def _reentry(user=DELEGATED_USER, key=SIGNING_KEY):
    """What the MCP sends back on a tool call: the identity this edge signed onto the /mcp hop."""
    return identity_token.signed_headers(key, user)[identity_token.HEADER]


def test_a_delegation_bearer_is_refused_on_rest_routes_called_directly():
    """A worker that takes its token out of its own config and calls REST routes itself is refused:
    the token's audience is the MCP, and what a worker may do is what the MCP's tools do."""
    client, downstream = _client()
    routes = [("GET", "/meetings"), ("DELETE", "/meetings/1"), ("POST", "/bots"),
              ("POST", "/meetings/1/share"), ("POST", "/bots/google_meet/abc/speak"),
              ("GET", "/user/webhook")]
    if AGENT_CARRIED:
        routes += [("GET", "/agent/sessions"), ("POST", "/agent/chat"), ("POST", "/agent/chat/submit")]
    for method, path in routes:
        r = client.request(method, path, headers={"X-API-Key": DELEGATED}, json={})
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json() == {"detail": "a worker's delegation token is accepted on /mcp only"}
    assert downstream.last is None


def test_the_mcp_re_entry_resolves_the_same_bearer_on_rest_routes():
    """The MCP's tools call back into the edge with the caller's bearer and the identity the edge
    signed onto the /mcp hop; that hop is admitted and acts as the person the worker acts for."""
    client, downstream = _client()
    r = client.get("/meetings", headers={"X-API-Key": DELEGATED,
                                         "X-Vexa-Internal-Mcp-Identity": _reentry()})
    assert r.status_code == 200
    fwd = downstream.last["headers"]
    claims = identity_token.verify(VERIFY_KEY, fwd[identity_token.HEADER])
    assert claims["sub"] == "42" and claims["delegation"]["regime"] == "autonomous"
    # the marker is an authority header: it never travels past the edge
    assert "x-vexa-internal-mcp-identity" not in fwd


def test_a_re_entry_marker_must_be_the_edges_own_signature_for_the_same_delegation():
    client, downstream = _client()
    other_key = identity_token.generate_signing_key()
    person_own = {k: v for k, v in DELEGATED_USER.items() if k != "delegation"}
    wider = {**DELEGATED_USER, "delegation": {"regime": "human", "workspaces": "*"}}
    someone_else = {**DELEGATED_USER, "user_id": 43}
    for marker in (_reentry(key=other_key), _reentry(user=person_own), _reentry(user=wider),
                   _reentry(user=someone_else), "v1.forged.token", ""):
        r = client.get("/meetings", headers={"X-API-Key": DELEGATED,
                                             "X-Vexa-Internal-Mcp-Identity": marker})
        assert r.status_code == 403, marker
    expired = identity_token.sign(SIGNING_KEY, identity_token.claims_from_validation(DELEGATED_USER),
                                  now=1_000_000)
    r = client.get("/meetings", headers={"X-API-Key": DELEGATED,
                                         "X-Vexa-Internal-Mcp-Identity": expired})
    assert r.status_code == 403
    assert downstream.last is None


def test_without_a_signing_key_there_is_no_re_entry():
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=DELEGATED_USER, valid_key=DELEGATED), downstream, FakeRedis())
    r = TestClient(app).get("/meetings", headers={"X-API-Key": DELEGATED,
                                                  "X-Vexa-Internal-Mcp-Identity": _reentry()})
    assert r.status_code == 403
    assert downstream.last is None


def test_a_person_s_own_key_needs_no_marker_and_ignores_one():
    client, downstream = _client(user={"user_id": 7, "scopes": ["bot", "tx"], "max_concurrent": 3},
                                 key=VALID_KEY)
    r = client.get("/meetings", headers={"X-API-Key": VALID_KEY,
                                         "X-Vexa-Internal-Mcp-Identity": "anything"})
    assert r.status_code == 200
    assert "x-vexa-internal-mcp-identity" not in downstream.last["headers"]


def test_a_vxd_bearer_is_held_to_the_mcp_door_even_if_identity_answers_without_a_delegation():
    """The prefix alone marks the token's audience, so an identity answer that lost the ceiling
    cannot turn a worker's token into a person's key."""
    client, downstream = _client(user={"user_id": 42, "scopes": ["bot", "tx"], "max_concurrent": 3})
    assert client.get("/meetings", headers={"X-API-Key": DELEGATED}).status_code == 403
    assert client.post("/mcp", headers={"Authorization": f"Bearer {DELEGATED}"}, json={}).status_code == 200


def test_a_delegation_bearer_opens_no_identity_read_and_no_socket():
    client, _ = _client()
    assert client.get("/auth/me", headers={"X-API-Key": DELEGATED}).status_code == 403
    with client.websocket_connect("/ws", headers={"X-API-Key": DELEGATED}) as ws:
        assert ws.receive_json() == {"type": "error", "error": "invalid_api_key"}


def test_api_key_mcp_traffic_reaches_the_same_server():
    client, downstream = _client(user={"user_id": 7, "scopes": ["bot", "tx"], "max_concurrent": 3},
                                 key=VALID_KEY)
    assert client.post("/mcp", headers={"X-API-Key": VALID_KEY}, json={}).status_code == 200
    assert downstream.last["url"] == "http://mcp:8010/mcp"


def test_create_app_has_no_second_mcp_upstream():
    import inspect

    assert "agent_mcp_url" not in inspect.signature(create_app).parameters


def test_the_worker_harness_files_friction_with_its_token_and_that_is_the_only_rest_door():
    """`worker/friction.py` posts a turn's friction straight to `/agent/friction` with the dispatch's
    token. That one route is admitted; its neighbours are not."""
    if not AGENT_CARRIED:
        return
    client, downstream = _client()
    r = client.post("/agent/friction", headers={"X-API-Key": DELEGATED}, json={"what_happened": "x"})
    assert r.status_code == 200
    assert downstream.last["url"] == "http://agent-api/api/friction"
    assert identity_token.verify(VERIFY_KEY, downstream.last["headers"][identity_token.HEADER])["sub"] == "42"
    for method, path in (("GET", "/agent/friction"), ("POST", "/agent/friction/x"),
                         ("POST", "/agent/frictions"), ("DELETE", "/agent/friction")):
        assert client.request(method, path, headers={"X-API-Key": DELEGATED}).status_code == 403, path
