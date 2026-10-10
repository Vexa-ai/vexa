"""A tool's call back into the gateway carries the identity the gateway signed onto the request.

The gateway admits a worker's delegation token on `/mcp` only. A tool acts by calling the gateway
again with the caller's own bearer, and the gateway admits that second request when it carries the
identity it signed onto the `/mcp` forward (`reentry.py`). These tests pin the carrying, end to end
through the mounted transport: in on `X-Vexa-Identity`, out on `X-Vexa-Internal-Mcp-Identity`, to
the gateway and to nothing else.
"""
from __future__ import annotations

import json
import pathlib

import httpx
from fastapi.testclient import TestClient

from vexa_mcp import create_app
from conftest import API_KEY, GATEWAY_URL, FakeGateway

SIGNED = "v1.c2lnbmVkLWNsYWltcw.c2lnbmF0dXJl"
OUT = "x-vexa-internal-mcp-identity"
REPO = pathlib.Path(__file__).resolve().parents[5]


def _session(client, extra):
    head = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json", **extra}
    r = client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}})
    head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
    client.post("/mcp", headers=head, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return head


def _call(client, head, name, **arguments):
    return client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}})


def test_a_tool_call_carries_the_signed_identity_of_the_mcp_request_back_to_the_gateway():
    gateway = FakeGateway()
    app = create_app(GATEWAY_URL, transport=httpx.MockTransport(gateway.handler))
    with TestClient(app) as client:
        head = _session(client, {"X-Vexa-Identity": SIGNED})
        assert _call(client, head, "get_bot_status").status_code == 200
    hop = gateway.requests[-1]
    assert hop.url.path == "/bots/status"
    assert hop.headers[OUT] == SIGNED
    assert hop.headers["x-api-key"] == API_KEY


def test_a_request_with_no_signed_identity_carries_none():
    gateway = FakeGateway()
    app = create_app(GATEWAY_URL, transport=httpx.MockTransport(gateway.handler))
    with TestClient(app) as client:
        head = _session(client, {})
        assert _call(client, head, "get_bot_status").status_code == 200
    assert OUT not in gateway.requests[-1].headers


def test_the_marker_is_held_per_request_and_never_leaks_into_the_next_one():
    gateway = FakeGateway()
    client = TestClient(create_app(GATEWAY_URL, transport=httpx.MockTransport(gateway.handler)))
    client.get("/bot-status", headers={"X-API-Key": API_KEY, "X-Vexa-Identity": SIGNED})
    assert gateway.requests[-1].headers[OUT] == SIGNED
    client.get("/bot-status", headers={"X-API-Key": API_KEY})
    assert OUT not in gateway.requests[-1].headers


AGENT_MANIFEST = json.loads((REPO / "core" / "agent" / "mcp.tools.v1.json").read_text())
AGENT_OPENAPI = json.loads((REPO / "core" / "agent" / "mcp.tools.v1.openapi.json").read_text())


def test_an_assembled_agent_tool_carries_it_through_the_gateway_too():
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"utc_now": "2026-10-09T00:00:00+00:00"})

    def discovery(request):
        if str(request.url) == "http://agent/.well-known/mcp-tools.json":
            return httpx.Response(200, json=AGENT_MANIFEST)
        if str(request.url) == "http://agent/openapi.json":
            return httpx.Response(200, json=AGENT_OPENAPI)
        return httpx.Response(404)

    app = create_app(GATEWAY_URL, transport=httpx.MockTransport(upstream),
                     assembly_env={"ADMIN_API_URL": "http://identity", "AGENT_API_URL": "http://agent"},
                     assembly_transport=httpx.MockTransport(discovery))
    r = TestClient(app).get("/tools/current_time", headers={"Authorization": "Bearer vxd_a.b.c",
                                                            "X-Vexa-Identity": SIGNED})
    assert r.status_code == 200
    assert str(seen[-1].url) == f"{GATEWAY_URL}/agent/time"
    assert seen[-1].headers[OUT] == SIGNED
