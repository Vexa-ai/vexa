"""D3 (0.13.4): default-deny for shared tokens — the route-declaration check and its enforcement.

A worker whose turn other people read (a room's turn, a shared chat's: delegation `scope.shared`, or
a workspace principal, D2) reaches ONLY the agent-api routes `core/agent/routes.v1.json`
declares `"shared": "allow"`. Every row and verb carries the declaration; every route the app serves
under the forward has one (`route_policy.assert_shared_declared`, which `create_app` runs at boot and
this file runs in CI); `route_policy.SHARED_GATE` refuses the rest, once, before the handler runs.

THE CASES ARE GENERATED from the built app and the manifest: every served route declared `refuse` is
called with a shared token whose ceiling grants the workspace it names — so nothing but this gate
stands in the way — and must answer the one `SHARED_REFUSAL` body. The allow-set is pinned: widening
it is an edit here, reviewed beside the route it opens.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from control_plane import identity_token, route_policy
from tests.test_delegation_ceiling_verbs import INSIDE, KEY, PERSON, _app_routes, _build, _person

AGENT = Path(__file__).resolve().parents[1]
SHARED = identity_token.SHARED_REFUSAL["reason"]

#: The routes a shared (room / shared-chat) token may reach — the room's workspace W through its
#: ceiling, the room's own call, the clock and the model catalog, the worker's friction report, and
#: proposing a claim (the write half of `/api/claims` only).
#: Everything else is refused. Widening this is a security decision: say why beside the row.
EXPECTED_ALLOWED = frozenset({
    ("GET", "/api/workspace/tree"), ("GET", "/api/workspace/file"), ("PUT", "/api/workspace/file"),
    ("POST", "/api/workspace/entity"), ("POST", "/api/workspace/asset"), ("GET", "/api/workspace/purpose"),
    ("GET", "/api/workspaces/{slug}/identity/state"), ("PUT", "/api/workspaces/{slug}/identity/icon"),
    ("POST", "/api/sessions/{session}/call/show"), ("POST", "/api/sessions/{session}/call/highlight"),
    ("GET", "/api/time"), ("GET", "/api/models/catalog"), ("POST", "/api/friction"),
    # a room's signal turn PROPOSES through the claim book (S1-E, D27); reading it stays refused
    ("POST", "/api/claims"),
    # a room reads its OWN history (int-s1); the handler refuses every other conversation to a
    # shared token or a workspace principal (`shared_conversation`, tests/test_chat_read.py)
    ("GET", "/api/chats/{session}/turns"),
})
#: Person-wide routes the security review named (C1) and the PRD lists: never a shared token's.
PERSON_WIDE = [
    ("GET", "/api/people/followups"), ("GET", "/api/people/discover"), ("GET", "/api/people/known"),
    ("GET", "/api/people/mentionable"), ("GET", "/api/claims"), ("GET", "/api/workspace/states"),
    ("GET", "/api/home"), ("GET", "/api/home/customer"), ("GET", "/api/home/history"),
    ("GET", "/api/notifications"), ("GET", "/api/usage"), ("GET", "/api/budgets/usage"),
    ("GET", "/api/chat/folders"), ("GET", "/api/chats/search"),
    ("GET", "/api/connections"), ("POST", "/api/connections/gmail/inbox"),
    ("GET", "/api/refs/resolve"), ("POST", "/api/agents/message"), ("GET", "/api/workspace/shared"),
]

APP_ROUTES = _app_routes()
ROUTES = {(m, r.path): r for r in APP_ROUTES for m in r.methods}
SERVED = sorted(k for k in ROUTES if k[1].startswith(route_policy.UPSTREAM_PREFIX) and k[0] != "HEAD")
REFUSED_CASES = [k for k in SERVED if k not in route_policy.SHARED_ALLOWED]


def _shared(workspaces=(INSIDE,), **extra) -> dict:
    dlg = {"regime": "human", "workspaces": list(workspaces), "shared": True, **extra}
    return {identity_token.HEADER: identity_token.sign(KEY, {"sub": PERSON, "email": "ada@example.com",
                                                             "delegation": dlg})}


def _ws_principal(workspaces=(INSIDE,), shared=True) -> dict:
    dlg = {"regime": "human", "workspaces": list(workspaces), "principal_kind": "workspace",
           "principal_workspace": INSIDE, "on_behalf_of": "8", "bills_to": 7}
    if shared:
        dlg["shared"] = True
    return {identity_token.HEADER: identity_token.sign(KEY, {"sub": PERSON, "delegation": dlg})}


def _call(client, key, headers):
    method, path = key
    path = re.sub(r"\{[^}]+\}", INSIDE, path)
    has_body = bool(ROUTES[key].dependant.body_params)
    return client.request(method, path, json={} if has_body else None, headers=headers)


@pytest.fixture
def client(monkeypatch, tmp_path):
    c = _build(monkeypatch, tmp_path)
    # every switchable feature on, so a feature gate never answers before this one does
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms,home2,readme_page,transcript_signals,levels,usage")
    return c


# ── the declaration check (CI) ──────────────────────────────────────────────────────────────────

def test_every_served_route_declares_shared():
    """The CI half of D3: a route agent-api serves under the forward with no `shared` declaration
    fails here (and the boot refuses it), naming the route."""
    route_policy.assert_shared_declared(APP_ROUTES)
    assert set(SERVED) <= set(route_policy.SHARED_TABLE)


def test_an_undeclared_route_fails_the_check():
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/api/brand-new/person-wide")
    def _new():  # pragma: no cover — never called
        return {}

    with pytest.raises(route_policy.PolicyError, match="GET /api/brand-new/person-wide"):
        route_policy.assert_shared_declared(app.routes)


def test_the_gate_prefix_is_the_manifests_forward():
    doc = json.loads((AGENT / "routes.v1.json").read_text())
    assert route_policy.UPSTREAM_PREFIX == doc["forward"]["upstream_prefix"]


def test_every_row_and_verb_in_the_manifest_carries_allow_or_refuse():
    doc = json.loads((AGENT / "routes.v1.json").read_text())
    for row in doc["routes"] + doc["verbs"]:
        assert row.get("shared") in ("allow", "refuse"), row


@pytest.mark.parametrize("bad", [None, True, "yes", ""])
def test_a_row_without_a_readable_declaration_is_refused_at_load(bad):
    doc = json.loads((AGENT / "routes.v1.json").read_text())
    row = dict(doc["routes"][0])
    if bad is None:
        row.pop("shared")
    else:
        row["shared"] = bad
    doc["routes"] = [row] + doc["routes"][1:]
    with pytest.raises(route_policy.PolicyError, match="shared must be allow or refuse"):
        route_policy.shared_table(doc)


def test_the_allow_set_is_exactly_the_reviewed_one():
    assert route_policy.SHARED_ALLOWED == EXPECTED_ALLOWED, sorted(route_policy.SHARED_ALLOWED ^ EXPECTED_ALLOWED)


@pytest.mark.parametrize("key", PERSON_WIDE, ids=[f"{m} {p}" for m, p in PERSON_WIDE])
def test_person_wide_routes_are_declared_refuse(key):
    assert route_policy.SHARED_TABLE.get(key) == "refuse", key


# ── enforcement, generated ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", REFUSED_CASES, ids=[f"{m} {p}" for m, p in REFUSED_CASES])
def test_a_shared_token_is_refused_on_every_route_not_declared_allow(client, key):
    r = _call(client, key, _shared())
    assert r.status_code == 403, (key, r.status_code, r.text[:200])
    assert r.json()["detail"]["reason"] == SHARED, r.text[:200]


@pytest.mark.parametrize("key", PERSON_WIDE, ids=[f"{m} {p}" for m, p in PERSON_WIDE])
def test_a_workspace_principal_is_refused_even_without_the_shared_header(client, key):
    """D2: a workspace principal is shared by construction, so a missing `shared` never opens a route."""
    r = _call(client, key, _ws_principal(shared=False))
    assert r.status_code == 403 and r.json()["detail"]["reason"] == SHARED, r.text[:200]


@pytest.mark.parametrize("key", sorted(EXPECTED_ALLOWED), ids=[f"{m} {p}" for m, p in sorted(EXPECTED_ALLOWED)])
def test_an_allowed_route_is_not_refused_by_the_gate(client, key):
    """The route may still refuse on its own terms (ceiling, membership, body) — only not with D3."""
    for headers in (_shared(), _ws_principal()):
        r = _call(client, key, headers)
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
        reason = detail.get("reason") if isinstance(detail, dict) else None
        assert reason != SHARED, (key, r.status_code, r.text[:200])


def test_a_private_worker_and_a_person_are_not_touched_by_the_gate(client):
    private = {identity_token.HEADER: identity_token.sign(KEY, {
        "sub": PERSON, "delegation": {"regime": "human", "workspaces": "*"}})}
    for headers in (private, _person()):
        r = client.get("/api/people/followups", headers=headers)
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
        assert not (isinstance(detail, dict) and detail.get("reason") == SHARED), r.text[:200]


def test_a_shared_room_reads_its_workspace_page_through_the_gate(client):
    """The allowed path works end to end: W's page, inside the ceiling, through a shared token."""
    r = client.post("/api/workspace/shared/new", json={"name": "Team"}, headers=_person())
    assert r.status_code == 201, r.text
    ws = r.json()["workspace_id"]
    assert client.put("/api/workspace/file", json={"slug": ws, "path": "plan.md", "content": "hello"},
                      headers=_person()).status_code == 200
    r = client.get("/api/workspace/file", params={"slug": ws, "path": "plan.md"}, headers=_shared([ws]))
    assert r.status_code == 200 and "hello" in r.text, r.text[:200]


# ── the same table reaches the MCP edge and the worker ──────────────────────────────────────────

def test_the_served_tool_manifest_stamps_every_tool_from_the_same_table(client):
    tools = client.get("/.well-known/mcp-tools.json").json()["tools"]
    assert tools
    for t in tools:
        key = (t["route"]["method"], t["route"]["path"])
        want = "allow" if key in route_policy.SHARED_ALLOWED else "refuse"
        assert t.get("shared") == want, (t["name"], key, t.get("shared"))
    names = {t["name"]: t["shared"] for t in tools}
    for wide in ("people_followups", "people_discover", "customer_profile", "notifications_list",
                 "home_history", "usage_summary", "chat_folders_list"):
        assert names.get(wide, "refuse") == "refuse", wide
    # chat_read is `allow` so a room can read its OWN history; the route refuses every other
    # conversation to a shared token (tests/test_int_s1_seams.py)
    assert names.get("chat_read") == "allow"


def test_the_workers_shared_toolbelt_is_derived_from_the_same_table():
    man = json.loads((AGENT / "mcp.tools.v1.json").read_text())
    worker = json.loads((AGENT / "worker" / "mcp_tools.v1.json").read_text())
    allowed = {t["name"] for t in man["tools"]
               if t.get("route") and (t["route"]["method"], t["route"]["path"]) in route_policy.SHARED_ALLOWED}
    # …plus the other domains' tools whose own manifest declares `shared: allow` (crm's read tools,
    # D4 filters their fields); those are not in the agent's `tools` list (a private turn reaches them
    # through the bare server prefix), so a shared turn must name them.
    others = set()
    for dom in ("crm", "flows", "meetings"):
        p = AGENT.parent / dom / "mcp.tools.v1.json"
        if p.exists():
            others |= {t["name"] for t in json.loads(p.read_text())["tools"] if t.get("shared") == "allow"}
    assert set(worker["shared_tools"]) == (allowed & set(worker["tools"])) | others


def test_a_shared_worker_attaches_named_tools_only_and_never_the_bare_prefix(monkeypatch, tmp_path):
    from shared import delegation
    from worker.engine import VEXA_MCP_SERVER, VEXA_MCP_SHARED_TOOLS, mcp_delegation_config
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("VEXA_MCP_URL", "http://mcp.test")
    for kw in ({"subject": "7", "shared": True},
               {"subject": "1042", "shared": True, "principal_kind": "workspace",
                "principal_workspace": "team", "on_behalf_of": "8"}):
        monkeypatch.setenv("VEXA_MCP_DELEGATION_TOKEN", delegation.mint_delegation(
            "k", regime="human", workspaces=["team"], **kw))
        _path, tools = mcp_delegation_config(work)
        assert f"mcp__{VEXA_MCP_SERVER}" not in tools
        assert tools == [f"mcp__{VEXA_MCP_SERVER}__{t}" for t in VEXA_MCP_SHARED_TOOLS]
    monkeypatch.setenv("VEXA_MCP_DELEGATION_TOKEN", delegation.mint_delegation(
        "k", subject="7", regime="human", workspaces="*"))
    _path, tools = mcp_delegation_config(work)
    assert tools[0] == f"mcp__{VEXA_MCP_SERVER}", "a private turn keeps its whole toolbelt"


def test_claims_is_declared_per_verb_propose_allowed_read_refused(client):
    """S1-E: a room's signal turn proposes through `POST /api/claims`; the owner's context notes
    (`GET /api/claims`) stay refused to every shared token."""
    assert route_policy.SHARED_TABLE[("POST", "/api/claims")] == "allow"
    assert route_policy.SHARED_TABLE[("GET", "/api/claims")] == "refuse"
    for headers in (_shared(), _ws_principal()):
        r = client.get("/api/claims", headers=headers)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == SHARED
        r = client.post("/api/claims", json={}, headers=headers)
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
        assert not (isinstance(detail, dict) and detail.get("reason") == SHARED), r.text[:200]
