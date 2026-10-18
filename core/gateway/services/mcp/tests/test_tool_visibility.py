"""Which MCP tools a caller is SHOWN and may CALL (`visibility.py`, VEXA_MCP_TOOLS_*).

The production bar for an upgrade: an existing key sees exactly the tool list it saw before, and a
tool it does not see cannot be reached by naming it either. These tests drive `/mcp` the way an
agent does (initialize, tools/list, tools/call), with the deployment's settings passed in.
"""
from __future__ import annotations

import json
import pathlib

import httpx
from fastapi.testclient import TestClient

from vexa_mcp import create_app
from vexa_mcp import visibility

from conftest import GATEWAY_URL, MEETINGS_MANIFEST, MEETINGS_OPENAPI
from test_assembled_surface import FLOWS_MANIFEST, FLOWS_OPENAPI

#: The 0.12 surface, as the 0.12 rehearsal captured it for every scoped key (names only).
V012 = json.loads((pathlib.Path(__file__).parent / "fixtures" / "tools-list-v0.12.json").read_text())

HIDDEN_IN_PROD = ("get_meeting", "get_meeting_participants", "delete_meeting")


def _domains(request: httpx.Request) -> httpx.Response:
    """A production-shaped deployment: meetings and flows assembled (identity has no manifest)."""
    manifests = {"meeting-api.test": (MEETINGS_MANIFEST, MEETINGS_OPENAPI),
                 "flows.test": (FLOWS_MANIFEST, FLOWS_OPENAPI)}
    if request.url.host in manifests:
        manifest, openapi = manifests[request.url.host]
        if request.url.path == "/.well-known/mcp-tools.json":
            return httpx.Response(200, json=manifest)
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json=openapi)
    return httpx.Response(404)


def _app(gateway_seen, **tools_env):
    def upstream(request):
        gateway_seen.append(request)
        return httpx.Response(200, json={"ok": True})
    return create_app(
        GATEWAY_URL, transport=httpx.MockTransport(upstream),
        assembly_env={"ADMIN_API_URL": "http://identity.test", "MEETING_API_URL": "http://meeting-api.test",
                      "FLOWS_API_URL": "http://flows.test", "VEXA_FLOWS_API_KEY": "test-operator-key",
                      "VEXA_MCP_BOOT_PROBE_PAUSE_SECONDS": "0"},
        assembly_transport=httpx.MockTransport(_domains),
        # The release default: no unproven feature switched on (production's value).
        features_env={"VEXA_FEATURES": ""},
        visibility_env=tools_env)


def _session(client, extra=None):
    head = {"Authorization": "Bearer k", "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json", **(extra or {})}
    r = client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}})
    head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
    client.post("/mcp", headers=head, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return head


def _list(client, head):
    return client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).json()["result"]["tools"]


def _call(client, head, name, arguments):
    return client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}}).json()["result"]


PROD = {"VEXA_MCP_TOOLS_ALLOW": ",".join(V012)}
COHORT_PERSON = {"x-user-id": "77", "x-user-email": "Tester@Example.org"}


# ── the contract: an existing key's tools/list is the 0.12 list ────────────────────────────────
def test_prod_values_list_exactly_the_012_tools_for_an_ordinary_key():
    with TestClient(_app([], **PROD)) as client:
        names = [t["name"] for t in _list(client, _session(client))]
    assert sorted(names) == V012


def test_prod_values_do_not_change_a_single_listed_descriptor():
    with TestClient(_app([])) as full, TestClient(_app([], **PROD)) as prod:
        unfiltered = {t["name"]: t for t in _list(full, _session(full))}
        filtered = _list(prod, _session(prod))
    assert filtered, "nothing listed"
    for tool in filtered:
        assert json.dumps(tool, sort_keys=True) == json.dumps(unfiltered[tool["name"]], sort_keys=True)
    # and the order is the server's own order, not a re-sort
    assert [t["name"] for t in filtered] == [n for n in unfiltered if n in set(V012)]


def test_the_013_additions_are_exactly_what_prod_values_hold_back():
    with TestClient(_app([])) as full:
        names = {t["name"] for t in _list(full, _session(full))}
    for name in HIDDEN_IN_PROD:
        assert name in names
    assert set(V012) <= names


# ── a hidden tool cannot be called by naming it ────────────────────────────────────────────────
def test_a_hidden_tool_called_directly_is_refused_like_an_unknown_tool_and_nothing_is_forwarded():
    seen = []
    with TestClient(_app(seen, **PROD)) as client:
        head = _session(client)
        hidden = _call(client, head, "delete_meeting", {"meeting_db_id": 4242, "asked_by_a_human": True})
        unknown = _call(client, head, "no_such_tool_xyz", {})
    assert hidden["isError"] is True
    assert seen == []
    # Indistinguishable from a tool that does not exist (no leak of its existence).
    assert json.dumps(hidden).replace("delete_meeting", "X") == json.dumps(unknown).replace("no_such_tool_xyz", "X")


def test_every_hidden_tool_is_refused():
    seen = []
    with TestClient(_app(seen, **PROD)) as client:
        head = _session(client)
        for name in HIDDEN_IN_PROD:
            result = _call(client, head, name, {"meeting_db_id": 1})
            assert result["isError"] is True, name
            assert f"Unknown tool: {name}" in json.dumps(result), name
    assert seen == []


def test_a_listed_tool_still_works_under_prod_values():
    seen = []
    with TestClient(_app(seen, **PROD)) as client:
        head = _session(client)
        result = _call(client, head, "list_meetings", {})
    assert result.get("isError") is not True, json.dumps(result)
    assert seen, "list_meetings never reached the gateway"


# ── the deny-list holds for everyone, the cohort included ───────────────────────────────────────
def test_deny_list_hides_and_refuses_for_every_caller():
    env = {"VEXA_MCP_TOOLS_DENY": "speak_in_meeting", "VEXA_MCP_TOOLS_COHORT": "*"}
    seen = []
    with TestClient(_app(seen, **env)) as client:
        head = _session(client, COHORT_PERSON)
        names = {t["name"] for t in _list(client, head)}
        result = _call(client, head, "speak_in_meeting", {"platform": "google_meet",
                                                         "native_meeting_id": "abc-defg-hij", "text": "hi"})
    assert "speak_in_meeting" not in names
    assert result["isError"] is True
    assert seen == []


# ── the cohort: named people see the full surface ──────────────────────────────────────────────
def test_a_cohort_person_sees_the_full_surface_while_an_ordinary_key_sees_012():
    env = {**PROD, "VEXA_MCP_TOOLS_COHORT": "tester@example.org"}
    with TestClient(_app([])) as full:
        everything = [t["name"] for t in _list(full, _session(full))]
    with TestClient(_app([], **env)) as client:
        cohort = [t["name"] for t in _list(client, _session(client, COHORT_PERSON))]
        ordinary = [t["name"] for t in _list(client, _session(client, {"x-user-id": "5",
                                                                        "x-user-email": "a@b.org"}))]
    assert cohort == everything
    assert sorted(ordinary) == V012


def test_a_cohort_person_may_call_a_tool_hidden_from_everyone_else():
    env = {**PROD, "VEXA_MCP_TOOLS_COHORT": "@example.org"}
    seen = []
    with TestClient(_app(seen, **env)) as client:
        result = _call(client, _session(client, COHORT_PERSON), "get_meeting", {"meeting_db_id": 9})
    assert "Unknown tool" not in json.dumps(result)
    assert seen


def test_a_caller_with_no_identity_is_never_in_the_cohort():
    env = {**PROD, "VEXA_MCP_TOOLS_COHORT": "77"}
    with TestClient(_app([], **env)) as client:
        names = [t["name"] for t in _list(client, _session(client))]
    assert sorted(names) == V012


# ── unset: nothing changes ─────────────────────────────────────────────────────────────────────
def test_unset_settings_change_nothing():
    with TestClient(_app([])) as a, TestClient(_app([], VEXA_MCP_TOOLS_ALLOW="", VEXA_MCP_TOOLS_DENY="",
                                                    VEXA_MCP_TOOLS_COHORT="")) as b:
        assert _list(a, _session(a)) == _list(b, _session(b))


# ── the parse ──────────────────────────────────────────────────────────────────────────────────
def test_the_parse():
    p = visibility.Policy.from_env({"VEXA_MCP_TOOLS_ALLOW": " a, b ,,", "VEXA_MCP_TOOLS_DENY": "b",
                                    "VEXA_MCP_TOOLS_COHORT": "7, X@Y.org, @corp.example"})
    assert p.allow == frozenset({"a", "b"}) and p.deny == frozenset({"b"})
    assert p.shown("a", {}) and not p.shown("b", {}) and not p.shown("c", {})
    assert p.shown("c", {"x-user-id": "7"})
    assert p.shown("c", {"x-user-email": "x@y.org"})
    assert p.shown("c", {"x-user-email": "q@corp.example"})
    assert not p.shown("b", {"x-user-id": "7"})
    assert visibility.Policy.from_env({}).active is False


def test_an_allow_list_naming_a_tool_the_server_does_not_have_boots_and_says_so(caplog):
    with TestClient(_app([], VEXA_MCP_TOOLS_ALLOW="list_meetings,not_a_tool")) as client:
        names = [t["name"] for t in _list(client, _session(client))]
    assert names == ["list_meetings"]
    assert "not_a_tool" in caplog.text
