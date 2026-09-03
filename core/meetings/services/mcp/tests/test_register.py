"""REGISTRATION — an assembled tool becomes a tool an agent can actually call.

A bound tool is turned into one FastAPI route on this service, carrying `operation_id=<tool name>`,
which is exactly how this service's own fourteen tools become MCP tools (`FastApiMCP` reads the
OpenAPI it generates). One mechanism for both, so an assembled tool and a built-in one are
indistinguishable to a client — which is the point of assembling rather than proxying.

The forward carries THE CALLER'S IDENTITY and nothing else: the bearer the edge already resolved,
travelling as `X-API-Key`, exactly as the fourteen do. There is no second credential and no way to
pass one as an argument (PRD 40.8).
"""
from __future__ import annotations

import httpx
import pytest

from vexa_mcp import bind, register
from vexa_mcp import manifest as m
from fastapi import FastAPI
from fastapi.testclient import TestClient

FLOWS_OPENAPI = {"paths": {
    "/flows": {"get": {"summary": "Every flow version", "parameters": []}},
    "/reactions": {"get": {"summary": "The operator projection", "parameters": [
        {"name": "status", "in": "query", "schema": {"type": "string"}}]}},
    "/reactions/{reaction_id}/{verb}": {"post": {"summary": "Steer one reaction", "parameters": []}},
}}
MANIFEST = {
    "contract": "mcp.tools.v1", "domain": "flows", "source": "oss", "owner": "core/flows",
    "base_url_env": "FLOWS_API_URL", "served_at": "/.well-known/mcp-tools.json",
    "depends_on": ["identity"],
    "tools": [
        {"name": "flows_list", "identity": "operator", "auth": "subject", "requires": ["identity", "flows"],
         "route": {"method": "GET", "path": "/flows"}},
        {"name": "reactions_list", "identity": "operator", "auth": "subject", "requires": ["identity", "flows"],
         "route": {"method": "GET", "path": "/reactions"}, "arguments": ["status"]},
        {"name": "reaction_signal", "identity": "user", "auth": "subject", "requires": ["identity", "flows"],
         "route": {"method": "POST", "path": "/reactions/{reaction_id}/{verb}"}},
    ],
}


@pytest.fixture()
def wired():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": str(request.url)})

    app = FastAPI()
    assembly = m.assemble([MANIFEST], deployed={"identity", "flows"})
    bound = bind.verify(assembly, {"flows": FLOWS_OPENAPI})
    register.register(app, bound, {"flows": "http://flows"},
                      transport=httpx.MockTransport(handler))
    return app, seen


def test_every_assembled_tool_gets_a_route_named_after_it(wired):
    app, _seen = wired
    ids = {getattr(r, "operation_id", None) for r in app.routes}
    assert {"flows_list", "reactions_list", "reaction_signal"} <= ids


def test_calling_the_tool_forwards_to_the_owning_domain(wired):
    app, seen = wired
    r = TestClient(app).get("/tools/flows_list", headers={"X-API-Key": "k"})
    assert r.status_code == 200, r.text
    assert str(seen[-1].url) == "http://flows/flows"


def test_the_caller_s_identity_travels_and_no_other_credential_does(wired):
    """PRD 40.8: one authentication path. The bearer the edge resolved goes on, and nothing else."""
    app, seen = wired
    TestClient(app).get("/tools/flows_list", headers={"X-API-Key": "the-caller-key"})
    fwd = seen[-1]
    assert fwd.headers.get("x-api-key") == "the-caller-key"
    assert "token" not in str(fwd.url).lower()


def test_a_declared_argument_travels_as_a_query_parameter(wired):
    app, seen = wired
    TestClient(app).get("/tools/reactions_list?status=blocked", headers={"X-API-Key": "k"})
    assert "status=blocked" in str(seen[-1].url)


def test_an_undeclared_argument_is_not_forwarded(wired):
    """An argument the route ignores is a success the agent reports for something that did not
    happen — so it never leaves this process."""
    app, seen = wired
    TestClient(app).get("/tools/reactions_list?status=blocked&invented=1",
                        headers={"X-API-Key": "k"})
    assert "invented" not in str(seen[-1].url)


def test_path_parameters_are_substituted(wired):
    app, seen = wired
    r = TestClient(app).post("/tools/reaction_signal", json={"reaction_id": "r1", "verb": "retry"},
                             headers={"X-API-Key": "k"})
    assert r.status_code == 200, r.text
    assert str(seen[-1].url) == "http://flows/reactions/r1/retry"


def test_a_missing_credential_is_refused_before_the_forward(wired):
    app, seen = wired
    before = len(seen)
    r = TestClient(app).get("/tools/flows_list")
    assert r.status_code == 401
    assert len(seen) == before, "nothing was forwarded"


def test_the_domain_s_own_error_reaches_the_caller_unchanged():
    """A 403 from flows is flows' answer, and an agent needs to see it. Rewriting it as a 500 would
    turn "you may not do that" into "we are broken"."""
    app = FastAPI()
    assembly = m.assemble([MANIFEST], deployed={"identity", "flows"})
    bound = bind.verify(assembly, {"flows": FLOWS_OPENAPI})
    register.register(app, bound, {"flows": "http://flows"},
                      transport=httpx.MockTransport(
                          lambda r: httpx.Response(403, json={"detail": "operator only"})))
    r = TestClient(app).get("/tools/flows_list", headers={"X-API-Key": "k"})
    assert r.status_code == 403 and "operator only" in r.text
