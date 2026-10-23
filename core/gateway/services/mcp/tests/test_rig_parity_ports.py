"""Vexa-ai/vexa#1783 — the control rig's product verbs, served by the ONE MCP instead.

`deploy/dogfood/rig/vexa_control_mcp.py` served these on a separate process that reached the stack
around its doors. Each already had a domain route behind the gateway; what was missing was the tool.
They arrive here two ways, both thin forwards with the caller's own credential (ADR-0036 decision 4,
ADR-0037):

  * meetings — native tools in this service: `get_meeting` (rig `meeting_info`) and
    `get_meeting_participants` (rig `meeting_participants`);
  * agent — manifest rows in `core/agent/mcp.tools.v1.json`: `workspace_push`, `workspace_pull`,
    `workspace_attach`, bound from agent-api's own OpenAPI.

A git token is never a tool argument: the routes accept one in the body, but the edge forwards only
declared arguments and the manifest refuses a credential-shaped one.
"""
from __future__ import annotations

import json
import pathlib

import httpx
from fastapi.testclient import TestClient

from vexa_mcp import create_app

REPO = pathlib.Path(__file__).resolve().parents[5]
AGENT_MANIFEST = json.loads((REPO / "core" / "agent" / "mcp.tools.v1.json").read_text())
AGENT_OPENAPI = json.loads((REPO / "core" / "agent" / "mcp.tools.v1.openapi.json").read_text())


# ── meetings: native tools ──────────────────────────────────────────────────────────────────────

def test_get_meeting_reads_exactly_one_row_by_its_db_id(client, gateway, auth):
    r = client.get("/meeting", params={"meeting_db_id": 4242}, headers=auth)
    assert r.status_code == 200
    hop = gateway.requests[-1]
    assert (hop.method, hop.url.path) == ("GET", "/meetings/4242")


def test_get_meeting_without_an_id_is_refused_before_any_hop(client, gateway, auth):
    before = len(gateway.requests)
    r = client.get("/meeting", headers=auth)
    assert r.status_code == 422
    assert len(gateway.requests) == before


def test_get_meeting_participants_addresses_the_room(client, gateway, auth):
    r = client.get("/meeting-participants",
                   params={"platform": "teams", "native_meeting_id": "9361792952021"}, headers=auth)
    assert r.status_code == 200
    hop = gateway.requests[-1]
    assert (hop.method, hop.url.path) == ("GET", "/meetings/teams/9361792952021/participants")


def test_get_meeting_participants_defaults_to_google_meet(client, gateway, auth):
    client.get("/meeting-participants", params={"native_meeting_id": "abc-defg-hij"}, headers=auth)
    assert gateway.requests[-1].url.path == "/meetings/google_meet/abc-defg-hij/participants"


def test_get_meeting_participants_refuses_an_unknown_platform(client, gateway, auth):
    before = len(gateway.requests)
    r = client.get("/meeting-participants",
                   params={"platform": "webex", "native_meeting_id": "x"}, headers=auth)
    assert r.status_code == 422
    assert len(gateway.requests) == before


# ── meetings: delete_meeting (rig `meeting_delete`) ──────────────────────────────────────────────
# Destructive, so it carries the speaking gate twice over: a required `asked_by_a_human` the model
# must set deliberately, and meeting-api's own `require_person` on the route, which refuses a worker
# dispatched without a person (regime not `human`) — that refusal is passed through unchanged.

def test_delete_meeting_deletes_exactly_one_row_by_its_db_id(client, gateway, auth):
    gateway.routes[("DELETE", "/meetings/4242")] = (204, None)
    r = client.request("DELETE", "/meeting", params={"meeting_db_id": 4242, "asked_by_a_human": True},
                       headers=auth)
    assert r.status_code == 200, r.text
    hop = gateway.requests[-1]
    assert (hop.method, hop.url.path) == ("DELETE", "/meetings/4242")
    assert r.json()["deleted"] is True and r.json()["meeting_db_id"] == 4242


def test_delete_meeting_is_refused_unless_a_human_asked(client, gateway, auth):
    before = len(gateway.requests)
    for gate in ({}, {"asked_by_a_human": False}):
        r = client.request("DELETE", "/meeting", params={"meeting_db_id": 4242, **gate}, headers=auth)
        assert r.status_code == 422, gate
    assert len(gateway.requests) == before


def test_delete_meeting_without_an_id_is_refused_before_any_hop(client, gateway, auth):
    before = len(gateway.requests)
    r = client.request("DELETE", "/meeting", params={"asked_by_a_human": True}, headers=auth)
    assert r.status_code == 422
    assert len(gateway.requests) == before


def test_delete_meeting_passes_the_unwatched_worker_refusal_through(client, gateway, auth):
    refusal = {"code": "person_required", "reason": "needs a person in the loop"}
    gateway.routes[("DELETE", "/meetings/7")] = (403, {"detail": refusal})
    r = client.request("DELETE", "/meeting", params={"meeting_db_id": 7, "asked_by_a_human": True},
                       headers=auth)
    assert r.status_code == 403
    assert r.json()["detail"] == refusal


# ── agent: manifest rows ────────────────────────────────────────────────────────────────────────

PORTED_AGENT = {
    "workspace_push": ("POST", "/api/workspace/push"),
    "workspace_pull": ("POST", "/api/workspace/pull"),
    "workspace_status": ("POST", "/api/workspace/status"),
    "workspace_attach": ("POST", "/api/workspace/shared/{workspace_id}/attach"),
}


def _assembled(upstream):
    def discovery(request):
        if str(request.url) == "http://agent/.well-known/mcp-tools.json":
            return httpx.Response(200, json=AGENT_MANIFEST)
        if str(request.url) == "http://agent/openapi.json":
            return httpx.Response(200, json=AGENT_OPENAPI)
        return httpx.Response(404)
    return create_app("http://gateway.test", transport=httpx.MockTransport(upstream),
                      assembly_env={"ADMIN_API_URL": "http://identity", "AGENT_API_URL": "http://agent"},
                      assembly_transport=httpx.MockTransport(discovery))


def test_the_git_workspace_verbs_are_manifest_rows_on_their_agent_routes():
    rows = {t["name"]: t for t in AGENT_MANIFEST["tools"]}
    for name, (method, path) in PORTED_AGENT.items():
        assert name in rows, f"{name} is not declared by the agent domain"
        assert (rows[name]["route"]["method"], rows[name]["route"]["path"]) == (method, path)


def test_no_git_verb_takes_a_token_argument():
    app = _assembled(lambda r: httpx.Response(200, json={}))
    tools = {t.name: (t.inputSchema if hasattr(t, "inputSchema") else t.input_schema)
             for t in app.state.mcp.tools}
    for name in PORTED_AGENT:
        assert name in tools, f"{name} missing from tools/list"
        assert "token" not in (tools[name] or {}).get("properties", {}), name


def test_workspace_push_forwards_through_the_gateway_as_the_caller():
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"remote": "origin", "branch": "main", "head_sha": "a" * 40})
    r = TestClient(_assembled(upstream)).post("/tools/workspace_push", json={"slug": "acme"},
                                              headers={"x-api-key": "fixture-user-key"})
    assert r.status_code == 200
    assert str(seen[-1].url) == "http://gateway.test/agent/workspace/push"
    assert seen[-1].headers["x-api-key"] == "fixture-user-key"
    assert json.loads(seen[-1].content) == {"slug": "acme"}


def test_workspace_pull_forwards_through_the_gateway_as_the_caller():
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"updated": True})
    r = TestClient(_assembled(upstream)).post("/tools/workspace_pull", json={"slug": "acme"},
                                              headers={"x-api-key": "fixture-user-key"})
    assert r.status_code == 200
    assert str(seen[-1].url) == "http://gateway.test/agent/workspace/pull"


def test_workspace_status_forwards_through_the_gateway_and_reads_as_a_look():
    """The look-only half of a sync: the tool reaches agent-api's status route as the caller, and the
    description an agent reads says it changes nothing and comes before pull and push."""
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"branch": "main", "ahead": 0, "behind": 2})
    app = _assembled(upstream)
    tool = next(t for t in app.state.mcp.tools if t.name == "workspace_status")
    assert "workspace_pull" in tool.description and "workspace_push" in tool.description
    assert "without changing" in tool.description
    r = TestClient(app).post("/tools/workspace_status", json={"slug": "acme"},
                             headers={"x-api-key": "fixture-user-key"})
    assert r.status_code == 200
    assert str(seen[-1].url) == "http://gateway.test/agent/workspace/status"
    assert seen[-1].headers["x-api-key"] == "fixture-user-key"
    assert json.loads(seen[-1].content) == {"slug": "acme"}


def test_workspace_attach_puts_the_workspace_in_the_path_and_the_repo_in_the_body():
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"state": "cloned"})
    r = TestClient(_assembled(upstream)).post(
        "/tools/workspace_attach", params={"workspace_id": "w123"},   # a path argument rides the query here
        json={"repo": "https://github.com/acme/kb", "ref": "main"},
        headers={"x-api-key": "fixture-user-key"})
    assert r.status_code == 200
    assert str(seen[-1].url) == "http://gateway.test/agent/workspace/shared/w123/attach"
    assert json.loads(seen[-1].content) == {"repo": "https://github.com/acme/kb", "ref": "main"}


# ── identity: the person's settings and workspace regime (rig `settings`, `workspace_regime`) ───

IDENTITY_PKG = REPO / "core" / "identity" / "services" / "admin-api" / "src" / "admin_api"
IDENTITY_MANIFEST = json.loads((IDENTITY_PKG / "mcp.tools.v1.json").read_text())
IDENTITY_OPENAPI = json.loads((IDENTITY_PKG / "mcp.tools.v1.openapi.json").read_text())


def _assembled_with_identity(upstream):
    def discovery(request):
        if str(request.url) == "http://identity/.well-known/mcp-tools.json":
            return httpx.Response(200, json=IDENTITY_MANIFEST)
        if str(request.url) == "http://identity/openapi.json":
            return httpx.Response(200, json=IDENTITY_OPENAPI)
        return httpx.Response(404)
    return create_app("http://gateway.test", transport=httpx.MockTransport(upstream),
                      assembly_env={"ADMIN_API_URL": "http://identity"},
                      assembly_transport=httpx.MockTransport(discovery))


def test_identity_serves_the_settings_and_regime_tools():
    app = _assembled_with_identity(lambda r: httpx.Response(200, json={}))
    names = {t.name for t in app.state.mcp.tools}
    assert {"settings", "settings_set", "workspace_regime", "workspace_regime_set"} <= names


def test_settings_set_forwards_to_the_user_door_as_the_caller():
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"changed": {"mail_minutes": False}})
    r = TestClient(_assembled_with_identity(upstream)).put(
        "/tools/settings_set", json={"setting": "mail_minutes", "value": "off"},
        headers={"x-api-key": "fixture-user-key"})
    assert r.status_code == 200, r.text
    assert (seen[-1].method, str(seen[-1].url)) == ("PUT", "http://gateway.test/user/settings")
    assert seen[-1].headers["x-api-key"] == "fixture-user-key"
    assert json.loads(seen[-1].content) == {"setting": "mail_minutes", "value": "off"}
