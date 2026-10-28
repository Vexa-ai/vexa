"""Whether a domain's tools are called back through the gateway, and at which path, is the domain's
declaration — `forward` in its `mcp.tools.v1` manifest — never a domain name this assembler knows.

A domain the gateway fronts wholesale (the agent) declares
`"forward": {"edge_prefix": "/agent/", "upstream_prefix": "/api/"}`, the same mapping its
`routes.v1` gives the gateway. Its tools then go to the gateway at `<edge_prefix><rest>`, carrying
the identity the gateway signed onto the request (`reentry.py`), instead of to the domain's own
door. A domain that declares no forward is called at its door, whatever it is called.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vexa_mcp import bind, register
from vexa_mcp import manifest as m

OPENAPI = {"paths": {"/api/time": {"get": {"summary": "now", "parameters": []}},
                     "/api/notes/{note_id}": {"get": {"summary": "one note", "parameters": []}}}}
FORWARD = {"edge_prefix": "/agent/", "upstream_prefix": "/api/"}


def _manifest(domain="agent", forward=FORWARD, paths=("/api/time", "/api/notes/{note_id}")):
    doc = {"contract": "mcp.tools.v1", "domain": domain, "source": "oss", "owner": f"core/{domain}",
           "base_url_env": f"{domain.upper()}_API_URL", "served_at": "/.well-known/mcp-tools.json",
           "depends_on": ["identity"],
           "tools": [{"name": f"t{i}", "identity": "user", "auth": "subject",
                      "requires": ["identity", domain], "route": {"method": "GET", "path": p}}
                     for i, p in enumerate(paths)]}
    if forward is not None:
        doc["forward"] = forward
    return doc


@pytest.mark.parametrize("forward", [
    {"edge_prefix": "agent/", "upstream_prefix": "/api/"},
    {"edge_prefix": "/agent/", "upstream_prefix": "/api"},
    {"edge_prefix": "/agent/"},
    "gateway",
])
def test_a_forward_names_two_absolute_prefixes(forward):
    with pytest.raises(m.ManifestError, match="forward"):
        m.validate(_manifest(forward=forward))


def test_every_tool_of_a_forwarded_domain_lives_under_the_upstream_prefix():
    with pytest.raises(m.ManifestError, match="forward"):
        m.validate(_manifest(paths=("/api/time", "/time")))


def _wired(doc, gateway_url="http://gateway.test"):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    app = FastAPI()
    domain = doc["domain"]
    bound = bind.verify(m.assemble([doc], deployed={"identity", domain}), {domain: OPENAPI})
    register.register(app, bound, {domain: f"http://{domain}-door"},
                      transport=httpx.MockTransport(handler), gateway_url=gateway_url)
    return TestClient(app), seen


def test_a_forwarded_domain_s_tools_go_through_the_gateway_at_the_declared_path():
    client, seen = _wired(_manifest())
    assert client.get("/tools/t0", headers={"X-API-Key": "k"}).status_code == 200
    assert str(seen[-1].url) == "http://gateway.test/agent/time"
    assert client.get("/tools/t1", params={"note_id": "a/b"}, headers={"X-API-Key": "k"}).status_code == 200
    assert str(seen[-1].url) == "http://gateway.test/agent/notes/a%2Fb"


def test_the_declared_prefixes_are_the_ones_used():
    client, seen = _wired(_manifest(forward={"edge_prefix": "/desk/", "upstream_prefix": "/api/"}))
    client.get("/tools/t0", headers={"X-API-Key": "k"})
    assert str(seen[-1].url) == "http://gateway.test/desk/time"


def test_the_agent_domain_without_a_forward_is_called_at_its_own_door():
    client, seen = _wired(_manifest(forward=None))
    client.get("/tools/t0", headers={"X-API-Key": "k"})
    assert str(seen[-1].url) == "http://agent-door/api/time"


def test_any_domain_that_declares_a_forward_is_called_through_the_gateway():
    client, seen = _wired(_manifest(domain="flows"))
    client.get("/tools/t0", headers={"X-API-Key": "k"})
    assert str(seen[-1].url) == "http://gateway.test/agent/time"


def test_with_no_gateway_configured_a_forwarded_domain_is_called_at_its_door():
    client, seen = _wired(_manifest(), gateway_url=None)
    client.get("/tools/t0", headers={"X-API-Key": "k"})
    assert str(seen[-1].url) == "http://agent-door/api/time"
