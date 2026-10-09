"""Edge policy is the owning domain's declaration, not the edge's code.

Two facts a domain used to leave the gateway to spell:

  * WHERE A WORKER'S DELEGATION TOKEN IS ADMITTED. A `routes.v1` row says `"delegation": true` when
    a worker's own bearer may call it — the MCP front door, and the agent's friction report. Every
    other row refuses that bearer (unless it is the MCP's re-entry, `delegation.py`).
  * HOW A FORWARDED DOMAIN'S PUBLIC PATHS MAP ONTO ITS SERVICE. The agent domain declares
    `"forward": {"edge_prefix": "/agent/", "upstream_prefix": "/api/"}`; the edge registers the
    domain's rows from its manifest (a literal row is a route of its own, `"stream": true` relays
    server-sent events, a `{path:path}` row is the catch-all) and forwards each under the declared
    upstream prefix.

The tests below change the DATA and watch the edge follow it, so the policy cannot quietly move
back into code.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from gateway import ROUTE_SCOPES, create_app, routes_manifest
from gateway.routes_manifest import ManifestError

from conftest import FakeAuthorizer, FakeDownstream, FakeRedis, VALID_KEY, needs_agent

DELEGATED = "vxd_header.payload.signature"
DELEGATED_USER = {"user_id": 42, "scopes": ["bot", "tx"], "max_concurrent": 3,
                  "delegation": {"regime": "autonomous", "workspaces": ["ws_1"]}}
PERSON = {"user_id": 7, "scopes": ["bot", "tx"], "max_concurrent": 3}
REFUSED = {"detail": "a worker's delegation token is accepted on /mcp only"}

_REAL_ROOT = routes_manifest._repo_root()


def _doc(domain, rows, **extra):
    return {"contract": "routes.v1", "domain": domain, "routes": rows, **extra}


# ── the manifest rules ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["yes", 1, None, []])
def test_delegation_is_a_boolean(value):
    with pytest.raises(ManifestError) as e:
        routes_manifest.assemble([_doc("mcp", [{"method": "POST", "path": "/mcp",
                                                "scopes": ["bot"], "delegation": value}])])
    assert "delegation" in str(e.value)


def test_an_unscoped_row_cannot_admit_a_delegation():
    """An unscoped row is identity-only and never reaches the authorizer that reads the flag."""
    with pytest.raises(ManifestError):
        routes_manifest.assemble([_doc("gateway", [{"method": "GET", "path": "/auth/me",
                                                    "scopes": [], "delegation": True}])])


def test_the_delegation_rows_are_exactly_the_declared_ones():
    a = routes_manifest.assemble([_doc("mcp", [
        {"method": "POST", "path": "/mcp", "scopes": ["bot"], "delegation": True},
        {"method": "GET", "path": "/mcp", "scopes": ["bot"], "delegation": False},
        {"method": "PUT", "path": "/mcp", "scopes": ["bot"]}])])
    assert a.delegation == {("POST", "/mcp")}


@pytest.mark.parametrize("forward", [
    {"edge_prefix": "agent/", "upstream_prefix": "/api/"},
    {"edge_prefix": "/agent", "upstream_prefix": "/api/"},
    {"edge_prefix": "/agent/", "upstream_prefix": ""},
    {"edge_prefix": "/agent/"},
    "/agent/ -> /api/",
])
def test_a_forward_names_two_absolute_prefixes(forward):
    with pytest.raises(ManifestError) as e:
        routes_manifest.assemble([_doc("agent", [{"method": "GET", "path": "/agent/{path:path}",
                                                  "scopes": ["bot"]}], forward=forward)])
    assert "forward" in str(e.value)


@pytest.mark.parametrize("path", ["/agents/x", "/agent/{id}/x", "/api/x"])
def test_a_forwarded_domain_declares_only_rows_its_forward_can_serve(path):
    """Every row of a forwarded domain is a literal under the edge prefix or the prefix's catch-all;
    anything else is a route the edge would have to know how to serve by name."""
    with pytest.raises(ManifestError):
        routes_manifest.assemble([_doc("agent", [{"method": "GET", "path": path, "scopes": ["bot"]}],
                                       forward={"edge_prefix": "/agent/", "upstream_prefix": "/api/"})])


def test_stream_is_a_boolean_on_a_literal_row():
    with pytest.raises(ManifestError):
        routes_manifest.assemble([_doc("agent", [{"method": "GET", "path": "/agent/{path:path}",
                                                  "scopes": ["bot"], "stream": True}],
                                       forward={"edge_prefix": "/agent/", "upstream_prefix": "/api/"})])
    with pytest.raises(ManifestError):
        routes_manifest.assemble([_doc("agent", [{"method": "GET", "path": "/agent/x",
                                                  "scopes": ["bot"], "stream": "sse"}],
                                       forward={"edge_prefix": "/agent/", "upstream_prefix": "/api/"})])


@needs_agent
def test_the_shipped_manifests_admit_a_worker_on_the_mcp_door_and_the_friction_report_only():
    a = routes_manifest.load({"gateway", "meetings", "identity", "mcp", "agent"})
    assert a.delegation == {k for k, d in a.owner_of.items() if d == "mcp"} | {("POST", "/agent/friction")}
    assert a.forwards == {"agent": ("/agent/", "/api/")}


# ── the edge follows the data ────────────────────────────────────────────────────────────────────

def _concrete(path: str) -> str:
    return path.replace("{path:path}", "x").replace("{", "").replace("}", "").replace(
        "meeting_id", "1").replace("recording_id", "1").replace("media_file_id", "1")


def test_a_delegation_bearer_is_admitted_exactly_where_a_row_says_so():
    """Generated over the whole table: a worker's own bearer, with no re-entry, is refused on
    every route but the declared ones."""
    declared = routes_manifest.load(
        routes_manifest.carried({"gateway", "meetings", "identity", "mcp", "agent"})).delegation
    downstream = FakeDownstream()
    client = TestClient(create_app(FakeAuthorizer(user=DELEGATED_USER, valid_key=DELEGATED),
                                   downstream, FakeRedis()))
    checked = 0
    for (method, template) in sorted(ROUTE_SCOPES):
        r = client.request(method, _concrete(template), headers={"X-API-Key": DELEGATED}, json={})
        refused = r.status_code == 403 and r.headers.get("content-type", "").startswith(
            "application/json") and r.json() == REFUSED
        assert refused is ((method, template) not in declared), (method, template, r.status_code)
        checked += 1
    assert checked == len(ROUTE_SCOPES) and declared


def _tree(tmp_path, agent_doc=None, mcp_doc=None):
    """A repo root carrying the real manifests, with the agent or mcp one replaced."""
    for d, path in routes_manifest.manifest_paths(_REAL_ROOT).items():
        if not path.exists():
            continue
        dst = tmp_path / path.relative_to(_REAL_ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        doc = json.loads(path.read_text())
        if d == "agent" and agent_doc is not None:
            doc = agent_doc(doc)
        if d == "mcp" and mcp_doc is not None:
            doc = mcp_doc(doc)
        dst.write_text(json.dumps(doc))
    return tmp_path


def _client(monkeypatch, root, user=DELEGATED_USER, key=DELEGATED):
    monkeypatch.setattr(routes_manifest, "_repo_root", lambda: root)
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=user, valid_key=key), downstream, FakeRedis(),
                     agent_api_url="http://agent-api")
    return TestClient(app), downstream


@needs_agent
def test_without_the_flag_the_friction_report_refuses_a_worker(tmp_path, monkeypatch):
    def unflag(doc):
        for row in doc["routes"]:
            row.pop("delegation", None)
        return doc

    client, downstream = _client(monkeypatch, _tree(tmp_path, agent_doc=unflag))
    r = client.post("/agent/friction", headers={"X-API-Key": DELEGATED}, json={})
    assert (r.status_code, r.json()) == (403, REFUSED)
    assert downstream.last is None


@needs_agent
def test_a_new_literal_row_is_a_route_of_its_own_with_its_own_policy(tmp_path, monkeypatch):
    def add(doc):
        doc["routes"].append({"method": "POST", "path": "/agent/notes", "scopes": ["bot", "tx"],
                              "delegation": True})
        return doc

    client, downstream = _client(monkeypatch, _tree(tmp_path, agent_doc=add))
    r = client.post("/agent/notes", headers={"X-API-Key": DELEGATED}, json={"a": 1})
    assert r.status_code == 200
    assert downstream.last["url"] == "http://agent-api/api/notes"
    # its catch-all neighbour still refuses the worker
    assert client.post("/agent/notes/x", headers={"X-API-Key": DELEGATED}, json={}).status_code == 403


@needs_agent
def test_the_mcp_door_is_the_mcp_manifest_s_declaration(tmp_path, monkeypatch):
    def unflag(doc):
        for row in doc["routes"]:
            row.pop("delegation", None)
        return doc

    client, downstream = _client(monkeypatch, _tree(tmp_path, mcp_doc=unflag))
    r = client.post("/mcp", headers={"Authorization": f"Bearer {DELEGATED}"}, json={})
    assert (r.status_code, r.json()) == (403, REFUSED)
    assert downstream.last is None


@needs_agent
def test_the_agent_domain_is_forwarded_under_its_declared_upstream_prefix(tmp_path, monkeypatch):
    def move(doc):
        doc["forward"] = {"edge_prefix": "/agent/", "upstream_prefix": "/v2/"}
        return doc

    client, downstream = _client(monkeypatch, _tree(tmp_path, agent_doc=move), user=PERSON,
                                 key=VALID_KEY)
    assert client.get("/agent/sessions", headers={"X-API-Key": VALID_KEY}).status_code == 200
    assert downstream.last["url"] == "http://agent-api/v2/sessions"
    assert client.post("/agent/friction", headers={"X-API-Key": VALID_KEY}, json={}).status_code == 200
    assert downstream.last["url"] == "http://agent-api/v2/friction"
