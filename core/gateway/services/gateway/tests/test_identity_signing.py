"""gateway-identity.v1 at the edge — the gateway signs the identity it resolved onto every forward.

agent-api and meeting-api believe `x-user-*` only with a valid `X-Vexa-Identity` beside it, so
these pin the producer half: every leg that stamps an identity (the buffered REST forward, the SSE
forward, the MCP relay, the `/ws` subscribe-authorization hop) carries a signature over exactly the
identity /internal/validate answered, and a client can neither supply one nor keep one.
"""
import asyncio
import json

import pytest

from fastapi.testclient import TestClient

from gateway import create_app, identity_token
from conftest import (SIGNING_KEY, VERIFY_KEY, VALID_KEY, VALID_USER, FakeAuthorizer, FakeDownstream,
                      FakeRedis, needs_agent)

AUTH = {"x-api-key": VALID_KEY}
USER = {**VALID_USER, "workspaces": ["ws_a", "ws_b"], "webhook_url": "https://hooks.example/x",
        "webhook_secret": "whsec", "webhook_events": {"meeting.completed": True}}


def _client(key=SIGNING_KEY, user=USER):
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=user), downstream, FakeRedis(), identity_key=key)
    return TestClient(app), downstream


def test_a_rest_forward_carries_a_signature_over_the_resolved_identity():
    client, downstream = _client()
    assert client.get("/meetings", headers=AUTH).status_code == 200
    fwd = downstream.last["headers"]
    claims = identity_token.verify(VERIFY_KEY, fwd[identity_token.HEADER])
    assert claims["sub"] == "7"
    assert claims["email"] == "u@example.com"
    assert claims["scopes"] == ["bot", "tx", "browser"]
    assert claims["limits"] == 3
    assert claims["workspaces"] == ["ws_a", "ws_b"]
    assert claims["webhook_url"] == "https://hooks.example/x"
    assert claims["exp"] - claims["iat"] == identity_token.DEFAULT_TTL_SEC
    # The plain headers ride beside it and say the same thing the signature does.
    assert fwd["x-user-id"] == "7"
    assert fwd["x-user-workspaces"] == "ws_a,ws_b"
    assert json.loads(fwd["x-user-webhook-events"]) == {"meeting.completed": True}


def test_a_client_supplied_signature_never_reaches_the_downstream():
    client, downstream = _client()
    forged = identity_token.sign(identity_token.generate_signing_key(), {"sub": "1"})
    client.get("/meetings", headers={**AUTH, identity_token.HEADER: forged, "x-user-id": "1"})
    fwd = downstream.last["headers"]
    assert fwd[identity_token.HEADER] != forged
    assert identity_token.verify(VERIFY_KEY, fwd[identity_token.HEADER])["sub"] == "7"


def test_a_limit_of_zero_is_carried_as_zero():
    """0 bots is a real limit (quota depleted) — it must not collapse into 'no limit'."""
    client, downstream = _client(user={**USER, "max_concurrent": 0})
    client.get("/meetings", headers=AUTH)
    assert identity_token.verify(VERIFY_KEY, downstream.last["headers"][identity_token.HEADER])["limits"] == 0
    assert downstream.last["headers"]["x-user-limits"] == "0"


@needs_agent
def test_the_agent_sse_forward_is_signed_too():
    client, downstream = _client()
    client.post("/agent/chat", headers=AUTH, json={"prompt": "hi"})
    assert identity_token.verify(VERIFY_KEY, downstream.last["headers"][identity_token.HEADER])["sub"] == "7"


def test_the_mcp_relay_is_signed_too():
    client, downstream = _client()
    client.post("/mcp", headers={"Authorization": f"Bearer {VALID_KEY}"}, json={})
    assert identity_token.verify(VERIFY_KEY, downstream.last["headers"][identity_token.HEADER])["sub"] == "7"


def test_without_a_key_the_edge_forwards_plain_headers_only():
    """The in-process harness shape: no signing key, so nothing is signed — and the services behind
    a real deployment refuse exactly this (their side of the contract)."""
    client, downstream = _client(key=None)
    client.get("/meetings", headers=AUTH)
    assert identity_token.HEADER not in downstream.last["headers"]
    assert downstream.last["headers"]["x-user-id"] == "7"


def test_the_ws_subscribe_authorization_hop_is_signed():
    httpx = pytest.importorskip("httpx")
    from gateway.adapters import AdminApiAuthorizer

    seen = {}

    def handler(request):
        if request.url.path == "/internal/validate":
            return httpx.Response(200, json=USER)
        seen.update(request.headers)
        return httpx.Response(200, json={"authorized": [], "errors": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    authz = AdminApiAuthorizer(client, "http://admin-api", "http://meeting-api", identity_key=SIGNING_KEY)
    asyncio.run(authz.authorize_subscribe(VALID_KEY, [{"platform": "google_meet", "native_id": "a"}]))
    assert identity_token.verify(VERIFY_KEY, seen[identity_token.HEADER])["sub"] == "7"
    assert seen["x-user-workspaces"] == "ws_a,ws_b"
