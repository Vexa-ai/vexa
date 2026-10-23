"""The caller's API key stays at this edge: what travels on is the identity it resolved to.

Every forward is swept — each declared route (the scope matrix's own list, so a new row is covered
the day it is declared), the agent's streamed chat, the MCP relay, the `/ws` subscribe hop and the
sign-in session's live view — with the key presented in BOTH carriers a client uses (`x-api-key` and
`authorization: Bearer`). The services that verify the signed identity receive neither. The domains
listed in `CREDENTIAL_CARRIED_TO` receive the key in exactly one carrier, `x-api-key`.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from gateway import create_app, identity_token
from gateway import routes_manifest
from gateway.app import CREDENTIAL_CARRIED_TO, CREDENTIAL_HEADERS
from conftest import VALID_KEY, VALID_USER, FakeAuthorizer, FakeDownstream, FakeRedis, needs_agent
from test_scope_matrix import CARRIED_CASES

BOTH_CARRIERS = {"x-api-key": VALID_KEY, "authorization": f"Bearer {VALID_KEY}"}
SIGNING_KEY = identity_token.generate_signing_key()
FULL = {**VALID_USER, "scopes": ["bot", "tx", "browser"]}


def _app(downstream, **kw):
    return create_app(FakeAuthorizer(user=FULL), downstream, FakeRedis(), identity_key=SIGNING_KEY, **kw)


def _owner(method, template):
    present = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent", "crm"})
    return present.owner_of.get((method, template))


def _assert_forward(headers, owner, where):
    lowered = {k.lower(): v for k, v in headers.items()}
    assert identity_token.HEADER in lowered, f"{where}: no signed identity on the forward"
    assert "authorization" not in lowered, f"{where}: the client's bearer travelled past the edge"
    if owner in CREDENTIAL_CARRIED_TO:
        assert lowered.get("x-api-key") == VALID_KEY, f"{where}: {owner} is handed the key"
    else:
        assert "x-api-key" not in lowered, f"{where} ({owner}): the key travelled past the edge"


@pytest.mark.parametrize("method,url,template", CARRIED_CASES, ids=[f"{m} {u}" for m, u, _ in CARRIED_CASES])
def test_no_forward_carries_the_key_to_a_service_that_verifies_the_identity(method, url, template):
    downstream = FakeDownstream()
    client = TestClient(_app(downstream))
    body = {} if method in ("POST", "PUT", "PATCH") else None
    client.request(method, url, headers=BOTH_CARRIERS, json=body)
    if downstream.last is None:
        pytest.skip(f"{method} {url} is answered at the edge, nothing is forwarded")
    _assert_forward(downstream.last["headers"], _owner(method, template), f"{method} {url}")


def test_the_sweep_reaches_the_services_that_never_see_the_key():
    """Negative control on the sweep: it forwards to meeting-api rows, and those carry no key."""
    downstream = FakeDownstream()
    TestClient(_app(downstream)).get("/meetings", headers=BOTH_CARRIERS)
    assert downstream.last is not None and "x-api-key" not in downstream.last["headers"]
    assert identity_token.verify(SIGNING_KEY.public_key(),
                                 downstream.last["headers"][identity_token.HEADER])["sub"] == "7"


@needs_agent
def test_the_agent_chat_stream_carries_no_key():
    downstream = FakeDownstream()
    TestClient(_app(downstream)).post("/agent/chat", headers=BOTH_CARRIERS, json={"prompt": "hi"})
    _assert_forward(downstream.last["headers"], "agent", "POST /agent/chat (SSE)")


def test_the_mcp_is_handed_the_key_once_as_x_api_key():
    """The MCP's tools call back as the caller, so it is handed the key — in one carrier."""
    downstream = FakeDownstream()
    client = TestClient(_app(downstream))
    client.post("/mcp", headers={"authorization": f"Bearer {VALID_KEY}"}, json={})
    _assert_forward(downstream.last["headers"], "mcp", "POST /mcp")
    with client.stream("GET", "/mcp", headers={"authorization": f"Bearer {VALID_KEY}",
                                               "accept": "text/event-stream"}) as r:
        list(r.iter_bytes())
    _assert_forward(downstream.last["headers"], "mcp", "GET /mcp (relay)")


def test_crm_rows_are_handed_the_key_once_as_x_api_key():
    downstream = FakeDownstream()
    TestClient(_app(downstream, crm_api_url="http://crm-api:8300")).post(
        "/crm/read", headers=BOTH_CARRIERS, json={"record_id": "r"})
    _assert_forward(downstream.last["headers"], "crm", "POST /crm/read")


def test_the_carried_set_names_only_domains_the_edge_fronts():
    present = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent", "crm"})
    assert CREDENTIAL_CARRIED_TO <= set(present.owner_of.values())
    assert {"meetings", "agent"}.isdisjoint(CREDENTIAL_CARRIED_TO)
    assert CREDENTIAL_HEADERS == {"x-api-key", "authorization"}


def test_the_ws_subscribe_hop_carries_no_key():
    httpx = pytest.importorskip("httpx")
    from gateway.adapters import AdminApiAuthorizer

    seen = {}

    def handler(request):
        if request.url.path == "/internal/validate":
            return httpx.Response(200, json=FULL)
        seen.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json={"authorized": [], "errors": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    authz = AdminApiAuthorizer(client, "http://admin-api", "http://meeting-api", identity_key=SIGNING_KEY)
    asyncio.run(authz.authorize_subscribe(VALID_KEY, [{"platform": "google_meet", "native_id": "a"}]))
    assert identity_token.HEADER in seen
    assert "x-api-key" not in seen and "authorization" not in seen
