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
    "/flows": {"get": {"summary": "Every flow version", "parameters": []},
              "post": {"summary": "Author a new flow version", "requestBody": {"content": {
                  "application/json": {"schema": {
                      "type": "object", "title": "FlowSubmission",
                      "properties": {"name": {"type": "string"}, "on_event": {"type": "string"},
                                    "steps": {"type": "array", "items": {"type": "string"}}}}}}}}},
    "/reactions": {"get": {"summary": "The operator projection", "parameters": [
        {"name": "status", "in": "query", "schema": {"type": "string"}}]}},
    "/reactions/{reaction_id}/{verb}": {"post": {"summary": "Steer one reaction", "parameters": []}},
    "/things/{thing_id}": {"post": {"summary": "Annotate one thing", "parameters": [
        {"name": "dry_run", "in": "query", "schema": {"type": "boolean"}}],
        "requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "title": "Note", "properties": {"note": {"type": "string"}}}}}}}},
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
        {"name": "flows_submit", "identity": "operator", "auth": "subject",
         "requires": ["identity", "flows"], "route": {"method": "POST", "path": "/flows"},
         "arguments": ["name", "on_event", "steps"]},
        {"name": "annotate_thing", "identity": "user", "auth": "subject",
         "requires": ["identity", "flows"], "route": {"method": "POST", "path": "/things/{thing_id}"},
         "arguments": ["dry_run", "note"]},
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
    """As QUERY parameters, which is where they are now declared (issue #1468). They used to be
    read out of the JSON body, and being undeclared is exactly why an agent could not see them:
    the tool's input schema is derived from this route's parameters, so `reaction_signal` was
    published taking nothing and could not be addressed at all."""
    app, seen = wired
    r = TestClient(app).post("/tools/reaction_signal?reaction_id=r1&verb=retry",
                             headers={"X-API-Key": "k"})
    assert r.status_code == 200, r.text
    assert str(seen[-1].url) == "http://flows/reactions/r1/retry"


def test_a_path_parameter_is_required_rather_than_guessed(wired):
    app, seen = wired
    before = len(seen)
    r = TestClient(app).post("/tools/reaction_signal?reaction_id=r1", headers={"X-API-Key": "k"})
    assert r.status_code == 422 and len(seen) == before


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


# ── a requestBody-derived argument travels as a JSON body, not a query string ──────────────────────

def test_a_body_declared_argument_is_published_under_requestbody_on_this_edge(wired):
    """The mechanism that makes fastapi-mcp forward it as a body field: THIS edge's own OpenAPI has
    to show it under `requestBody`, not `parameters` — `Body(..., embed=True)` versus `Query(...)`."""
    app, _seen = wired
    op = app.openapi()["paths"]["/tools/flows_submit"]["post"]
    assert "requestBody" in op
    assert not any(p.get("name") in {"name", "on_event", "steps"} for p in op.get("parameters") or [])


def test_a_json_body_argument_travels_as_the_forwarded_requests_json_body(wired):
    app, seen = wired
    r = TestClient(app).post("/tools/flows_submit", json={"name": "n", "on_event": "e",
                                                          "steps": ["s1", "s2"]},
                             headers={"X-API-Key": "k"})
    assert r.status_code == 200, r.text
    fwd = seen[-1]
    assert fwd.url.path == "/flows"
    import json as _json
    assert _json.loads(fwd.content) == {"name": "n", "on_event": "e", "steps": ["s1", "s2"]}


def test_an_undeclared_body_field_is_not_forwarded(wired):
    """Same rule as an undeclared query argument: a field the manifest did not declare is dropped
    here, never guessed at downstream."""
    app, seen = wired
    import json as _json
    TestClient(app).post("/tools/flows_submit",
                         json={"name": "n", "on_event": "e", "steps": ["s1"], "invented": "x"},
                         headers={"X-API-Key": "k"})
    assert "invented" not in _json.loads(seen[-1].content)


def test_a_path_parameter_and_a_body_field_travel_on_the_same_call(wired):
    """`annotate_thing` mixes all three shapes at once — a path parameter, a query-origin argument
    and a body-origin one — the combination none of the single-shape tools exercise alone."""
    app, seen = wired
    r = TestClient(app).post("/tools/annotate_thing?thing_id=t1&dry_run=true",
                             json={"note": "looks fine"}, headers={"X-API-Key": "k"})
    assert r.status_code == 200, r.text
    fwd = seen[-1]
    assert fwd.url.path == "/things/t1"
    assert fwd.url.params.get("dry_run") == "true"
    import json as _json
    assert _json.loads(fwd.content) == {"note": "looks fine"}


def test_a_body_only_call_with_nothing_declared_still_passes_the_raw_body_through():
    """The pre-existing freeform shape (`reaction_signal`'s own generic body) is unchanged: a tool
    with NO declared body fields still forwards whatever JSON the caller sent, verbatim."""
    app = FastAPI()
    assembly = m.assemble([MANIFEST], deployed={"identity", "flows"})
    bound = bind.verify(assembly, {"flows": FLOWS_OPENAPI})
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    register.register(app, bound, {"flows": "http://flows"},
                      transport=httpx.MockTransport(handler))
    TestClient(app).post("/tools/reaction_signal?reaction_id=r1&verb=wake",
                         json={"reason": "operator override"}, headers={"X-API-Key": "k"})
    import json as _json
    assert _json.loads(seen[-1].content) == {"reason": "operator override"}
