"""D3 at the EDGE (0.13.4, review round 2 BL-3): a shared delegation reaches only rows declared
`"shared": "allow"`, in every domain's routes.v1, before any forward.

A room's or a shared chat's worker can read its own bearer and call this gateway directly
(security review H5), so the MCP manifest filter is a narrowing, not a control: this is the boundary.
The cases are GENERATED from the assembled table: every mcp_reentry row declared `refuse` is called
as a valid MCP re-entry of a shared delegation (so nothing but this check stands in the way) and
must be refused with nothing forwarded; every `allow` re-entry row is forwarded.
"""
import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from gateway import create_app, identity_token, routes_manifest
from conftest import AGENT_CARRIED, SIGNING_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

DELEGATED = "vxd_header.payload.signature"
SHARED = {"user_id": 42, "scopes": ["bot", "tx"], "max_concurrent": 3, "email": "ada@example.com",
          "delegation": {"regime": "human", "workspaces": ["ws_1"], "shared": True}}
WORKSPACE = {"user_id": 1042, "scopes": ["bot", "tx"], "max_concurrent": 0,
             "email": "ws-1042@principal.invalid",
             "delegation": {"regime": "human", "workspaces": ["ws_1"], "principal_kind": "workspace",
                            "principal_workspace": "ws_1", "on_behalf_of": "43", "bills_to": 42}}
PRIVATE = {**SHARED, "delegation": {"regime": "human", "workspaces": "*"}}

REPO = pathlib.Path(__file__).resolve().parents[5]


def _rows():
    out = []
    for p in sorted((REPO / "core").rglob("routes.v1.json")):
        if "node_modules" in p.parts:
            continue
        doc = json.loads(p.read_text())
        if doc.get("domain") == "agent" and not AGENT_CARRIED:
            continue
        for r in doc["routes"]:
            if r.get("mcp_reentry") or r.get("delegation"):
                out.append((r["method"], r["path"], r.get("shared")))
    return out


ROWS = _rows()
REFUSED = [(m, p) for m, p, s in ROWS if s != "allow"]
ALLOWED = [(m, p) for m, p, s in ROWS if s == "allow" and not p.startswith("/mcp")]


def _client(user):
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(user=user, valid_key=DELEGATED), downstream, FakeRedis(),
                     identity_key=SIGNING_KEY)
    return TestClient(app), downstream


def _call(client, user, method, path):
    fill = {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "account_id": "a" * 32,
            "meeting_id": "1", "recording_id": "5"}
    concrete = re.sub(r"\{([^}:]+)[^}]*\}", lambda m: fill.get(m.group(1), "x1"), path)
    reentry = identity_token.signed_headers(SIGNING_KEY, user)[identity_token.HEADER]
    return client.request(method, concrete, json={},
                          headers={"X-API-Key": DELEGATED, "X-Vexa-Internal-Mcp-Identity": reentry})


def test_every_manifest_row_declares_shared():
    for p in sorted((REPO / "core").rglob("routes.v1.json")):
        if "node_modules" in p.parts:
            continue
        for r in json.loads(p.read_text())["routes"]:
            assert r.get("shared") in ("allow", "refuse"), (p.name, r["method"], r["path"])


def test_the_table_reads_the_declaration_and_refuses_another_word():
    doc = json.loads((REPO / "core/identity/routes.v1.json").read_text())
    assert ("GET", "/user/settings") not in routes_manifest.assemble([doc]).shared_allow
    bad = {"contract": "routes.v1", "domain": "identity", "owner": "core/identity", "edge": "gateway",
           "routes": [{"method": "GET", "path": "/user/x", "scopes": ["bot"], "shared": True}]}
    with pytest.raises(routes_manifest.ManifestError, match="shared must be allow or refuse"):
        routes_manifest.assemble([bad])


@pytest.mark.parametrize("user", [SHARED, WORKSPACE], ids=["shared", "workspace-principal"])
@pytest.mark.parametrize("method,path", REFUSED, ids=[f"{m} {p}" for m, p in REFUSED])
def test_a_shared_delegation_is_refused_at_the_edge_on_every_row_not_declared_allow(user, method, path):
    client, downstream = _client(user)
    r = _call(client, user, method, path)
    assert r.status_code == 403, (method, path, r.status_code, r.text[:200])
    assert r.json()["detail"]["reason"] == "shared_conversation"
    assert downstream.last is None, "nothing is forwarded"


@pytest.mark.parametrize("method,path", ALLOWED, ids=[f"{m} {p}" for m, p in ALLOWED])
def test_a_shared_delegation_reaches_the_rows_declared_allow(method, path):
    client, downstream = _client(SHARED)
    r = _call(client, SHARED, method, path)
    assert r.status_code == 200, (method, path, r.status_code, r.text[:200])


def test_bl3_the_identity_settings_doors_refuse_a_room_token_at_the_edge():
    for user in (SHARED, WORKSPACE):
        client, downstream = _client(user)
        for method, path in (("GET", "/user/settings"), ("PUT", "/user/workspace-regime")):
            r = _call(client, user, method, path)
            assert r.status_code == 403 and downstream.last is None, (method, path)


def test_a_private_worker_is_not_touched_by_the_shared_rule():
    client, downstream = _client(PRIVATE)
    r = _call(client, PRIVATE, "GET", "/user/settings")
    assert r.status_code == 200


def test_the_mcp_front_door_admits_a_shared_delegation():
    client, downstream = _client(SHARED)
    r = client.post("/mcp", headers={"Authorization": f"Bearer {DELEGATED}"}, json={})
    assert r.status_code == 200


def test_n1_a_shared_delegation_reaches_a_transcript_by_id_carrying_its_shared_ceiling():
    """The edge forwards `GET /transcripts/by-id/{id}` to a shared turn (it is `shared: allow`), and
    the identity it signs says shared with the turn's ceiling — what meeting-api's door rebuilds and
    `shared_meeting_readable` refuses a private meeting on (meeting-api
    `test_shared_transcript_read.py::test_n1_*`)."""
    client, downstream = _client(SHARED)
    r = _call(client, SHARED, "GET", "/transcripts/by-id/{meeting_id}")
    assert r.status_code == 200 and downstream.last is not None
    sent = {k.lower(): v for k, v in dict(downstream.last.get("headers") or {}).items()}
    claims = identity_token.verify(SIGNING_KEY.public_key(), sent[identity_token.HEADER])
    assert claims["delegation"]["shared"] is True and claims["delegation"]["workspaces"] == ["ws_1"]
