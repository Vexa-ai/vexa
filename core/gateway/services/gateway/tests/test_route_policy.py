"""Edge policy is the owning domain's declaration, not the edge's code.

Two facts a domain used to leave the gateway to spell:

  * WHERE A WORKER'S DELEGATION TOKEN IS ADMITTED. A `routes.v1` row says `"delegation": true` when
    a worker's own bearer may call it — the MCP front door, and the agent's friction report — and
    `"mcp_reentry": true` when the MCP's tools call it back with that bearer (admitted only on the
    MCP's re-entry, `delegation.py`). Every other row refuses that bearer, re-entry or not.
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


@pytest.mark.parametrize("path", ["/agents/x", "/api/x", "/agent/{id:path}/x", "/agent/x{id}",
                                  "/agent/{id}/{id}", "/agent/{1d}/x", "/agent/x/{path:path}"])
def test_a_forwarded_domain_declares_only_rows_its_forward_can_serve(path):
    """Every row of a forwarded domain is a path under the edge prefix — literal segments and whole
    `{name}` parameters — or the prefix's catch-all; anything else is a route the edge would have to
    know how to serve by name."""
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


def test_a_forwarded_row_may_name_whole_segment_parameters():
    a = routes_manifest.assemble([_doc("agent", [{"method": "GET", "path": "/agent/jobs/{job_id}/status",
                                                  "scopes": ["bot"]}],
                                       forward={"edge_prefix": "/agent/", "upstream_prefix": "/api/"})])
    assert ("GET", "/agent/jobs/{job_id}/status") in a.scopes
    assert routes_manifest.params_of("/agent/jobs/{job_id}/status") == ("job_id",)
    assert routes_manifest.params_of("/agent/{path:path}") == ()


@pytest.mark.parametrize("value", ["yes", 1, None, []])
def test_mcp_reentry_is_a_boolean(value):
    with pytest.raises(ManifestError) as e:
        routes_manifest.assemble([_doc("meetings", [{"method": "GET", "path": "/meetings",
                                                     "scopes": ["tx"], "mcp_reentry": value}])])
    assert "mcp_reentry" in str(e.value)


def test_mcp_reentry_beside_delegation_refuses_to_boot():
    """A row that already admits a worker's token gains nothing from the narrower flag — and a flag
    that does nothing reads like a policy somebody chose."""
    with pytest.raises(ManifestError) as e:
        routes_manifest.assemble([_doc("mcp", [{"method": "POST", "path": "/mcp", "scopes": ["bot"],
                                                "delegation": True, "mcp_reentry": True}])])
    assert "mcp_reentry" in str(e.value)


def test_only_the_edge_s_own_unscoped_row_may_admit_the_mcp_re_entry():
    """An unscoped row never reaches the authorizer that reads the flag. The edge's own identity
    read (`/auth/me`) is the exception: its handler checks the flag itself."""
    with pytest.raises(ManifestError):
        routes_manifest.assemble([_doc("identity", [{"method": "GET", "path": "/x", "scopes": [],
                                                     "mcp_reentry": True}])])
    a = routes_manifest.assemble([_doc("gateway", [{"method": "GET", "path": "/auth/me", "scopes": [],
                                                    "mcp_reentry": True}])])
    assert a.mcp_reentry == {("GET", "/auth/me")}


def test_the_mcp_reentry_rows_are_exactly_the_declared_ones():
    a = routes_manifest.assemble([_doc("meetings", [
        {"method": "GET", "path": "/meetings", "scopes": ["tx"], "mcp_reentry": True},
        {"method": "POST", "path": "/meetings", "scopes": ["tx"], "mcp_reentry": False},
        {"method": "DELETE", "path": "/meetings/{meeting_id}", "scopes": ["tx"]}])])
    assert a.mcp_reentry == {("GET", "/meetings")}
    assert a.delegation == set()


#: The routes the MCP's own meeting tools call back into (`vexa_mcp/app.py`), and the identity
#: read its `auth: admin` tools ask first (`vexa_mcp/register.py`). The MCP package's
#: `tests/test_callback_routes.py` drives every one of those tools and holds each URL it sends to
#: a row in this set — so neither side can move without the other noticing.
MCP_CALLBACKS = {
    ("GET", "/auth/me"),
    ("POST", "/bots"), ("GET", "/bots/status"),
    ("DELETE", "/bots/{platform}/{native_meeting_id}"),
    ("GET", "/bots/{platform}/{native_meeting_id}/chat"),
    ("PUT", "/bots/{platform}/{native_meeting_id}/config"),
    ("POST", "/bots/{platform}/{native_meeting_id}/speak"),
    ("GET", "/meetings"),
    ("POST", "/meetings/{meeting_id}/annotate"),
    ("POST", "/meetings/{platform}/{native_meeting_id}/annotate"),
    ("GET", "/recordings"), ("GET", "/recordings/{recording_id}"),
    ("GET", "/transcripts/by-id/{meeting_id}"), ("GET", "/transcripts/search"),
    ("GET", "/transcripts/{platform}/{native_meeting_id}"),
}


def test_the_shipped_manifests_admit_the_mcp_re_entry_on_its_callback_routes_only():
    a = routes_manifest.load(routes_manifest.carried({"gateway", "meetings", "identity", "mcp", "agent"}))
    assert {k for k in a.mcp_reentry if a.owner_of[k] != "agent"} == MCP_CALLBACKS
    agent = {k for k in a.mcp_reentry if a.owner_of[k] == "agent"}
    # no catch-all ever carries it: re-entry on `/agent/{path:path}` would be every agent route
    assert not [k for k in agent if k[1].endswith(routes_manifest.CATCH_ALL)]
    assert not (a.mcp_reentry & a.delegation)


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


def test_the_mcp_re_entry_is_admitted_exactly_where_a_row_says_so():
    """Generated over the whole table, the re-entry half of the sweep above: a worker's bearer WITH
    a valid re-entry identity is admitted on a `delegation` row or an `mcp_reentry` row, and refused
    on every other one — proof that the MCP is calling is not proof that it calls THIS route."""
    from gateway import identity_token
    from conftest import SIGNING_KEY

    a = routes_manifest.load(routes_manifest.carried({"gateway", "meetings", "identity", "mcp", "agent"}))
    admitted = a.delegation | a.mcp_reentry
    marker = identity_token.signed_headers(SIGNING_KEY, DELEGATED_USER)[identity_token.HEADER]
    client = TestClient(create_app(FakeAuthorizer(user=DELEGATED_USER, valid_key=DELEGATED),
                                   FakeDownstream(), FakeRedis(), identity_key=SIGNING_KEY))
    checked = refused_n = 0
    for (method, template) in sorted(ROUTE_SCOPES):
        r = client.request(method, _concrete(template), json={},
                           headers={"X-API-Key": DELEGATED, "X-Vexa-Internal-Mcp-Identity": marker})
        refused = r.status_code == 403 and r.headers.get("content-type", "").startswith(
            "application/json") and r.json() == REFUSED
        assert refused is ((method, template) not in admitted), (method, template, r.status_code)
        checked += 1
        refused_n += refused
    assert checked == len(ROUTE_SCOPES) and refused_n and a.mcp_reentry


def _tree(tmp_path, agent_doc=None, mcp_doc=None, **docs):
    """A repo root carrying the real manifests, with the agent, mcp or any named one replaced."""
    docs = {**docs, "agent": agent_doc, "mcp": mcp_doc}
    for d, path in routes_manifest.manifest_paths(_REAL_ROOT).items():
        if not path.exists():
            continue
        dst = tmp_path / path.relative_to(_REAL_ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        doc = json.loads(path.read_text())
        if docs.get(d) is not None:
            doc = docs[d](doc)
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


def _unflag_reentry(doc):
    for row in doc["routes"]:
        row.pop("mcp_reentry", None)
    return doc


def _reentry_headers(user=DELEGATED_USER):
    from gateway import identity_token
    from conftest import SIGNING_KEY
    return {"X-API-Key": DELEGATED,
            "X-Vexa-Internal-Mcp-Identity":
                identity_token.signed_headers(SIGNING_KEY, user)[identity_token.HEADER]}


def _reentry_client(monkeypatch, root):
    from conftest import SIGNING_KEY
    monkeypatch.setattr(routes_manifest, "_repo_root", lambda: root)
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=DELEGATED_USER, valid_key=DELEGATED), downstream, FakeRedis(),
                     identity_key=SIGNING_KEY)
    return TestClient(app), downstream


def test_without_the_flag_the_mcp_s_callback_refuses_its_re_entry(tmp_path, monkeypatch):
    """The data decides: take `mcp_reentry` off the meetings rows and the MCP's own
    `get_meeting_transcript` hop is refused, re-entry identity and all."""
    client, downstream = _reentry_client(monkeypatch, _tree(tmp_path, meetings=_unflag_reentry))
    r = client.get("/transcripts/google_meet/abc-defg-hij", headers=_reentry_headers())
    assert (r.status_code, r.json()) == (403, REFUSED)
    assert downstream.last is None


def test_without_the_flag_the_identity_read_refuses_the_re_entry(tmp_path, monkeypatch):
    client, _ = _reentry_client(monkeypatch, _tree(tmp_path, gateway=_unflag_reentry))
    assert client.get("/auth/me", headers=_reentry_headers()).status_code == 403


def test_a_new_flagged_row_admits_the_re_entry_and_its_neighbour_does_not(tmp_path, monkeypatch):
    def flag(doc):
        for row in doc["routes"]:
            if (row["method"], row["path"]) == ("POST", "/meetings/{meeting_id}/share"):
                row["mcp_reentry"] = True
        return doc

    client, downstream = _reentry_client(monkeypatch, _tree(tmp_path, meetings=flag))
    assert client.post("/meetings/1/share", headers=_reentry_headers(), json={}).status_code == 200
    assert client.post("/transcripts/by-id/1/share", headers=_reentry_headers(),
                       json={}).status_code == 403


@needs_agent
def test_a_parameterised_agent_row_forwards_its_segment_re_encoded(tmp_path, monkeypatch):
    """`/agent/workspace/import/{operation_id}/status` is a route of its own: what fills the segment
    arrives downstream as one opaque segment, and a dot segment is refused as the catch-all
    refuses it."""
    client, downstream = _client(monkeypatch, _tree(tmp_path), user=PERSON, key=VALID_KEY)
    r = client.get("/agent/workspace/import/op%3Fx%23y/status", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 200
    assert downstream.last["url"] == "http://agent-api/api/workspace/import/op%3Fx%23y/status"


@needs_agent
@pytest.mark.parametrize("target", ["/agent/workspace/import/%2E%2E/status",
                                    "/agent/workspace/import/./status",
                                    "/agent/workspace%2Ftree", "/agent%2Ffriction"])
async def test_a_forwarded_row_refuses_what_its_catch_all_refuses(target):
    """Starlette matches on the DECODED path, so `/agent/workspace%2Ftree` reaches the literal row
    `/agent/workspace/tree`. The row holds the catch-all's rule — an encoded separator or a dot
    segment is a 400 — so a path answers the same whichever of the domain's rows it matched."""
    from urllib.parse import unquote
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=PERSON, valid_key=VALID_KEY), downstream, FakeRedis(),
                     agent_api_url="http://agent-api")
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    method = "POST" if target.endswith("friction") else "GET"
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
               "scheme": "http", "root_path": "", "query_string": b"", "path": unquote(target),
               "raw_path": target.encode(), "client": ("127.0.0.1", 1), "server": ("t", 80),
               "headers": [(b"host", b"t"), (b"x-api-key", VALID_KEY.encode()),
                           (b"content-type", b"application/json")]}, receive, send)
    assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 400
    assert downstream.last is None
