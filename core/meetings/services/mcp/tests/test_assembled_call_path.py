"""WHAT AN AGENT ACTUALLY GETS when it calls an assembled tool — issue #1468 C3.

Two questions, both asked at the mounted `/mcp` transport rather than at the forwarding route,
because the transport is what an agent sees and the two can disagree:

  1. **A refusal must arrive as a refusal.** The finding that opened this issue was a person's call
     to `reactions_list` coming back as JSON-RPC 200 with `Status code: 401` in the text. The 200 is
     the TRANSPORT and is correct — JSON-RPC carries tool failures inside a result — so the whole
     question is whether that result is marked `isError`. Pinned here, because nothing pinned it:
     the flag is produced inside `fastapi-mcp`, and a library that stops raising, or a forward that
     starts swallowing, turns every refusal success-shaped again with no test to notice.

  2. **A declared argument must reach the tool.** An assembled tool's route took `request` and
     nothing else, so this app's OpenAPI showed it with no parameters, so `tools/list` published an
     EMPTY input schema for it — and `additionalProperties: false` then refused every argument an
     agent passed. `reactions_list(status=…)` could not filter and `reaction_signal` could not be
     called AT ALL, because its `reaction_id` and `verb` had nowhere to go. The manifest declared
     them, `bind` verified them against the owning route, and the last step dropped them.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest

from vexa_mcp import create_app

REPO = pathlib.Path(__file__).resolve().parents[5]
FLOWS_MANIFEST = json.loads((REPO / "core" / "flows" / "mcp.tools.v1.json").read_text())

FLOWS_OPENAPI = {"paths": {
    "/flows": {"get": {"summary": "Every flow version the engine knows", "parameters": []}},
    "/reactions": {"get": {"summary": "Your share of the reaction queue", "parameters": [
        {"name": "status", "in": "query", "schema": {"type": "string"},
         "description": "admitted | running | failed | done"},
        {"name": "subject", "in": "query", "schema": {"type": "string"}}]}},
    "/reactions/{reaction_id}/{verb}": {"post": {"summary": "Steer one reaction",
                                                 "parameters": []}},
    "/timeline": {"get": {"summary": "One person's day, in order", "parameters": [
        {"name": "subject", "in": "query", "schema": {"type": "string"}},
        {"name": "since", "in": "query", "schema": {"type": "string"}},
        {"name": "until", "in": "query", "schema": {"type": "string"}},
        {"name": "limit", "in": "query", "schema": {"type": "integer"}}]}},
}}


def _discovery(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if not url.startswith("http://flows"):
        return httpx.Response(404)
    if url.endswith("/.well-known/mcp-tools.json"):
        return httpx.Response(200, json=FLOWS_MANIFEST)
    if url.endswith("/openapi.json"):
        return httpx.Response(200, json=FLOWS_OPENAPI)
    return httpx.Response(404)


@pytest.fixture
def wired():
    """The whole path: discovery, assembly, registration, the mounted `/mcp` transport — and an
    upstream whose answer each test chooses. `seen` is what flows actually received."""
    from fastapi.testclient import TestClient

    seen = []
    answer = {"status": 200, "json": {"ok": True}}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(answer["status"], json=answer["json"])

    app = create_app("http://gateway.test", transport=httpx.MockTransport(upstream),
                     assembly_env={"ADMIN_API_URL": "http://identity",
                                   "FLOWS_API_URL": "http://flows"},
                     assembly_transport=httpx.MockTransport(_discovery))
    ctx = TestClient(app)
    client = ctx.__enter__()
    head = {"Authorization": "Bearer person-key",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"}
    r = client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}})
    head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
    client.post("/mcp", headers=head, json={"jsonrpc": "2.0",
                                            "method": "notifications/initialized"})

    class Wired:
        tools = {t.name: t for t in app.state.mcp.tools}

        def call(self, name, **arguments):
            got = client.post("/mcp", headers=head, json={
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}})
            return got.json()["result"]

        def upstream_answers(self, status, body):
            answer.update(status=status, json=body)

        @property
        def seen(self):
            return seen

    try:
        yield Wired()
    finally:
        ctx.__exit__(None, None, None)


# ── 1 · a refusal arrives as a refusal ──────────────────────────────────────────────────────────

def test_an_upstream_refusal_arrives_marked_as_an_error(wired):
    wired.upstream_answers(401, {"detail": "X-Flows-Operator-Key required"})
    result = wired.call("reactions_list")
    assert result.get("isError") is True, result
    assert "401" in json.dumps(result)


def test_a_forbidden_reaches_the_agent_as_the_domains_own_answer(wired):
    """`that reaction is not yours` is flows' answer and the agent needs to see it — a 403 rewritten
    at the edge would turn "you may not do that" into "we are broken"."""
    wired.upstream_answers(403, {"detail": "that reaction is not yours"})
    result = wired.call("reaction_signal", reaction_id="r-someone-elses", verb="cancel")
    assert result.get("isError") is True
    assert "not yours" in json.dumps(result)


def test_an_upstream_success_is_not_marked_an_error(wired):
    """The control. A test that only asserts `isError` on a refusal passes on a server that marks
    everything an error."""
    wired.upstream_answers(200, {"reactions": []})
    result = wired.call("reactions_list")
    assert not result.get("isError"), result


# ── 2 · a declared argument reaches the tool ────────────────────────────────────────────────────

def test_a_declared_argument_is_published_in_the_tools_schema(wired):
    props = wired.tools["reactions_list"].inputSchema["properties"]
    assert set(props) == {"status", "subject"}, props


def test_a_path_parameter_is_published_and_required(wired):
    """`/reactions/{reaction_id}/{verb}` cannot be called without them, so an agent that cannot see
    them cannot call the tool at all — which is what "cancel the join you scheduled" needed."""
    schema = wired.tools["reaction_signal"].inputSchema
    assert set(schema["properties"]) == {"reaction_id", "verb"}
    assert set(schema.get("required") or []) == {"reaction_id", "verb"}


def test_an_argument_an_agent_passes_reaches_the_owning_route(wired):
    wired.call("reactions_list", status="failed")
    assert wired.seen[-1].url.params.get("status") == "failed"


def test_a_path_parameter_lands_in_the_path_not_in_the_query(wired):
    wired.call("reaction_signal", reaction_id="r-7", verb="cancel")
    assert wired.seen[-1].url.path == "/reactions/r-7/cancel"


def test_an_argument_the_tool_does_not_declare_is_refused_not_dropped(wired):
    """The other half, and the reason the schema is closed: an argument silently dropped is a
    success the agent reports for something that did not happen."""
    result = wired.call("reactions_list", nonesuch="x")
    assert result.get("isError") is True
    assert not wired.seen


def test_a_tool_with_no_declared_arguments_publishes_none(wired):
    assert wired.tools["flows_list"].inputSchema.get("properties") == {}
