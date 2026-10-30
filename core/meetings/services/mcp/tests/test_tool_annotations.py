"""#1646 — every tool in `tools/list` carries a title and EXPLICIT behaviour hints.

Read off the wire: the assembled app (built-ins + the repo's own flows manifest) is initialised over
streamable HTTP and asked `tools/list`, so what is asserted is what a client receives — not a
registry that might not have been applied.
"""
from __future__ import annotations

import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from test_assembled_surface import FLOWS_MANIFEST, FLOWS_OPENAPI, _boot
from vexa_mcp import annotations as ann
from vexa_mcp import create_app
from vexa_mcp import manifest as m

READ_ONLY = {
    "parse_meeting_link", "get_bot_status", "list_meetings", "get_meeting_transcript",
    "search_transcripts", "get_meeting_chat", "list_recordings", "get_recording",
    "whats_waiting", "timeline", "flows_list", "reactions_list", "friction_so_far",
}
DESTRUCTIVE = {"stop_bot", "speak_in_meeting", "reaction_signal"}
ADDITIVE_WRITES = {"request_meeting_bot", "annotate_meeting", "update_bot_config", "report_issue",
                   "report_friction"}
OPEN_WORLD = {"request_meeting_bot", "stop_bot", "speak_in_meeting", "report_issue"}


def _tools_list(app) -> list:
    head = {"Authorization": "Bearer k", "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"}
    with TestClient(app) as client:
        r = client.post("/mcp", headers=head, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}})
        head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        client.post("/mcp", headers=head, json={"jsonrpc": "2.0",
                                                "method": "notifications/initialized"})
        r = client.post("/mcp", headers=head,
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return r.json()["result"]["tools"]


@pytest.fixture(scope="module")
def listed():
    return {t["name"]: t for t in _tools_list(_boot(FLOWS_API_URL="http://flows"))}


def test_the_full_surface_is_listed(listed):
    assert len(listed) == 14 + len(FLOWS_MANIFEST["tools"])


def test_every_listed_tool_has_a_title_and_the_required_hints_explicitly(listed):
    """The contract: no tool leaves a client to the spec defaults (destructive, open-world)."""
    for name, tool in listed.items():
        assert len(name) <= ann.MAX_NAME, name
        assert isinstance(tool.get("title"), str) and tool["title"].strip(), name
        a = tool.get("annotations") or {}
        assert a.get("title") == tool["title"], name
        for hint in ann.REQUIRED_HINTS + ("idempotentHint",):
            assert isinstance(a.get(hint), bool), f"{name}: {hint} not explicit ({a})"


def test_no_shipped_tool_reaches_the_cautious_fallback():
    """Fail closed: a tool this repository ships with no annotation anywhere fails the suite."""
    app = _boot(FLOWS_API_URL="http://flows")
    assert app.state.unannotated_tools == []


def test_the_classification(listed):
    assert READ_ONLY | DESTRUCTIVE | ADDITIVE_WRITES == set(listed)
    for name, tool in listed.items():
        a = tool["annotations"]
        if name in READ_ONLY:
            assert a["readOnlyHint"] is True and a["destructiveHint"] is False, name
            assert a["idempotentHint"] is True, name
        else:
            assert a["readOnlyHint"] is False, name
            assert a["destructiveHint"] is (name in DESTRUCTIVE), name
        assert a["openWorldHint"] is (name in OPEN_WORLD), name


def test_the_flows_manifest_declares_its_own_annotations_and_they_match_the_registry():
    """The domain is the authority for its tools; the edge's registry is only the fallback. Keep
    them in step so a deployment pinned to an older flows image is classified the same."""
    for t in FLOWS_MANIFEST["tools"]:
        assert t.get("title"), t["name"]
        declared = t.get("annotations") or {}
        for hint in ann.REQUIRED_HINTS + ("idempotentHint",):
            assert isinstance(declared.get(hint), bool), (t["name"], hint)
            assert declared[hint] == ann.REGISTRY[t["name"]][hint], (t["name"], hint)


def test_a_manifest_annotation_wins_over_the_registry():
    doc = copy.deepcopy(FLOWS_MANIFEST)
    for t in doc["tools"]:
        if t["name"] == "timeline":
            t["title"] = "Your day"
            t["annotations"] = {"readOnlyHint": False, "destructiveHint": False,
                                "idempotentHint": False, "openWorldHint": True}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if not url.startswith("http://flows"):
            return httpx.Response(404)
        if url.endswith("/.well-known/mcp-tools.json"):
            return httpx.Response(200, json=doc)
        if url.endswith("/openapi.json"):
            return httpx.Response(200, json=FLOWS_OPENAPI)
        return httpx.Response(404)

    app = create_app("http://gateway.test",
                     transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                     assembly_env={"ADMIN_API_URL": "http://identity",
                                   "FLOWS_API_URL": "http://flows"},
                     assembly_transport=httpx.MockTransport(handler))
    tool = {t.name: t for t in app.state.mcp.tools}["timeline"]
    assert tool.title == "Your day"
    assert tool.annotations.readOnlyHint is False and tool.annotations.openWorldHint is True


def test_a_manifest_without_annotations_falls_back_to_the_registry():
    doc = copy.deepcopy(FLOWS_MANIFEST)
    for t in doc["tools"]:
        t.pop("title", None)
        t.pop("annotations", None)
    a = m.assemble([doc], deployed={"identity", "flows"})
    entries = {t.name: {"title": t.title, "annotations": t.annotations or {}} for t in a.tools}
    resolved = ann.resolve("whats_waiting", entries["whats_waiting"])
    assert resolved == ann.REGISTRY["whats_waiting"]


def test_an_unknown_unannotated_tool_is_served_cautiously():
    from mcp import types as mcp_types
    tool = mcp_types.Tool(name="private_thing", inputSchema={"type": "object"})
    fell_back = ann.apply([tool], {})
    assert fell_back == ["private_thing"]
    assert tool.title == "Private thing"
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.openWorldHint is True


@pytest.mark.parametrize("bad", [
    {"annotations": "yes"},
    {"annotations": {"readOnlyHint": "true"}},
    {"annotations": {"sideEffects": True}},
    {"title": ""},
])
def test_a_malformed_manifest_annotation_fails_validation(bad):
    doc = copy.deepcopy(FLOWS_MANIFEST)
    doc["tools"][0].update(bad)
    with pytest.raises(m.ManifestError):
        m.validate(doc)


def test_the_builtin_fourteen_alone_are_all_annotated():
    app = create_app("http://gateway.test",
                     transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert app.state.unannotated_tools == []
    for t in app.state.mcp.tools:
        assert t.annotations is not None and t.title, t.name
    json.dumps([t.model_dump(exclude_none=True) for t in app.state.mcp.tools])
