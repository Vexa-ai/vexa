"""The edge assembles its route table from the domains it fronts, and owns none of it.

PRD decision 40.5 — *"the gateway still owns nothing: it composes, strips authority, re-stamps,
forwards"* — and 40.7, which makes that concrete: **agents are optional**, so a table that names
`/agent/*` unconditionally cannot describe a `no-agents` deployment (40.6: gateway + meetings +
flows + identity).

What these tests hold down, in the order they matter:

  1. the FULL profile is byte-for-byte what shipped — 69 scoped rows and 2 unscoped, and every one
     of them still matched to a route that exists;
  2. without the agent domain the table lacks EXACTLY its seven rows and nothing else;
  3. an absent domain's routes are absent from the APP too, so a request 404s rather than 403s;
  4. the refusals that make the assembly safe to compose from files.
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from gateway import ROUTE_SCOPES, UNSCOPED_ROUTES, create_app, routes_manifest, undeclared_routes
from gateway.routes_manifest import ManifestError

from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

#: The agent domain's whole share of the edge — the seven rows that vanish in `no-agents`.
AGENT_ROWS = frozenset({
    ("POST", "/agent/chat"),
    ("GET", "/agent/meeting/stream"),
    ("GET", "/agent/{path:path}"),
    ("POST", "/agent/{path:path}"),
    ("PUT", "/agent/{path:path}"),
    ("PATCH", "/agent/{path:path}"),
    ("DELETE", "/agent/{path:path}"),
})
#: What shipped. A count, not a copy of the table: a second copy of 69 rows is a second thing to
#: keep in step, and `test_the_assembled_table_matches_the_app_exactly` is what proves the
#: CONTENT — against the routes themselves, which is a stronger anchor than a literal.
FULL_SCOPED, FULL_UNSCOPED = 69, 2


def _app(**kw):
    return create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis(), **kw)


# ── 1 · the full profile is unchanged ────────────────────────────────────────────────────────────

def test_the_full_profile_is_exactly_what_shipped():
    assert (len(ROUTE_SCOPES), len(UNSCOPED_ROUTES)) == (FULL_SCOPED, FULL_UNSCOPED)
    assert AGENT_ROWS <= set(ROUTE_SCOPES)


def test_the_assembled_table_matches_the_app_exactly():
    """Every declared row is a route, and every route is declared. This is the anchor: it ties the
    manifests to the thing they describe, so a row that drifts is caught by the routes themselves
    rather than by a copy of the table living in a test."""
    app = _app()
    registered = {(m, r.path) for r in app.routes if isinstance(r, APIRoute)
                  for m in (r.methods or ()) if m != "HEAD"}
    declared = set(ROUTE_SCOPES) | set(UNSCOPED_ROUTES)
    assert registered == declared, {
        "declared but not registered": sorted(declared - registered),
        "registered but not declared": sorted(registered - declared)}


def test_every_domain_declares_its_own_and_only_its_own():
    a = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent"})
    assert a.domains == {"agent": 7, "gateway": 2, "identity": 12, "mcp": 12, "meetings": 38}
    assert {k for k, d in a.owner_of.items() if d == "agent"} == AGENT_ROWS
    # The EDGE declares two routes and they are its own — /health and /auth/me forward nothing.
    assert {k for k, d in a.owner_of.items() if d == "gateway"} == set(UNSCOPED_ROUTES)


# ── 2 · the no-agents profile lacks exactly the agent's rows ─────────────────────────────────────

def test_without_the_agent_domain_the_table_lacks_exactly_its_seven_rows():
    full = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent"})
    lean = routes_manifest.load({"gateway", "meetings", "identity", "mcp"})
    assert set(full.scopes) - set(lean.scopes) == AGENT_ROWS
    assert set(lean.scopes) - set(full.scopes) == set()
    assert lean.unscoped == full.unscoped


def test_the_no_agents_app_does_not_register_the_agent_routes():
    app = _app(agent_api_url="")
    registered = {(m, r.path) for r in app.routes if isinstance(r, APIRoute)
                  for m in (r.methods or ()) if m != "HEAD"}
    assert registered & AGENT_ROWS == set()
    assert ("GET", "/bots") in registered, "only the agent domain went away"
    assert undeclared_routes(app, routes_manifest.load(
        {"gateway", "meetings", "identity", "mcp"}).scopes,
        routes_manifest.load({"gateway", "meetings", "identity", "mcp"}).unscoped) == []


# ── 3 · absent is 404, not 401 and not 403 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("post", "/agent/chat"), ("get", "/agent/meeting/stream"), ("get", "/agent/sessions"),
])
def test_a_request_to_an_absent_domain_is_404_not_401(method, path):
    """404 is the only honest answer: this deployment does not serve it. 401 would say "sign in and
    it will work" and 403 would say "you may not" — both describe a route that exists, and a client
    told either one retries, escalates, or asks for a scope nobody can grant."""
    client = TestClient(_app(agent_api_url=""))
    for headers in ({}, {"x-api-key": VALID_KEY}):
        r = getattr(client, method)(path, headers=headers)
        assert r.status_code == 404, (path, headers, r.status_code)


def test_the_same_request_is_served_when_the_agent_domain_is_there():
    """The half a blanket 404 would break."""
    client = TestClient(_app())
    assert client.get("/agent/sessions", headers={"x-api-key": VALID_KEY}).status_code != 404


# ── 4 · the refusals that make composing from files safe ─────────────────────────────────────────

def _doc(domain, rows):
    return {"contract": "routes.v1", "domain": domain, "routes": rows}


def test_two_domains_claiming_one_route_refuse_to_boot():
    with pytest.raises(ManifestError) as e:
        routes_manifest.assemble([
            _doc("meetings", [{"method": "GET", "path": "/x", "scopes": ["bot"]}]),
            _doc("agent", [{"method": "GET", "path": "/x", "scopes": ["tx"]}]),
        ])
    assert "meetings" in str(e.value) and "agent" in str(e.value), \
        "the refusal must name BOTH files, or the operator has two to find"


def test_a_scope_outside_the_vocabulary_refuses_to_boot():
    """An unknown scope is a set no key can hold — a deny that reads like a decision somebody made."""
    with pytest.raises(ManifestError) as e:
        routes_manifest.assemble([_doc("agent", [{"method": "GET", "path": "/x",
                                                  "scopes": ["bot", "amin"]}])])
    assert "amin" in str(e.value)


@pytest.mark.parametrize("row", [
    {"method": "FETCH", "path": "/x", "scopes": ["bot"]},
    {"method": "GET", "path": "agent/x", "scopes": ["bot"]},
])
def test_a_row_that_is_not_a_route_refuses_to_boot(row):
    with pytest.raises(ManifestError):
        routes_manifest.assemble([_doc("agent", [row])])


def test_a_manifest_of_the_wrong_contract_refuses_to_boot(tmp_path):
    p = tmp_path / "routes.v1.json"
    p.write_text('{"contract": "mcp.tools.v1", "domain": "agent", "routes": []}')
    with pytest.raises(ManifestError) as e:
        routes_manifest.read(p)
    assert "routes.v1" in str(e.value)


def test_a_deployed_domain_with_no_manifest_refuses_to_boot():
    with pytest.raises(ManifestError):
        routes_manifest.load({"gateway", "billing"})
