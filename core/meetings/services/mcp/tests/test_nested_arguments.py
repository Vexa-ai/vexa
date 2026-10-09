"""A tool argument whose type is a nested model travels to this edge's surface WHOLE.

`connection_request.setup` is a closed object on the owning route (agent-api's `SetupSpec`, the
credential broker's own shape): eight keys, `additionalProperties: false`, nested `oauth` and
`fields` closed too. Published as an open object, an agent filled it by guessing and invented a
`service` key the broker then refused as "invalid setup". `bind._inline` resolves the route's
`$ref`s and `register._publish_as` carries the result onto the tool, so the tool lists every allowed
key and the MCP refuses any other one by name, before anything is forwarded.
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
KEYS = {"oauth", "documentation_url", "endpoint", "header", "scheme", "method", "secret_label", "fields"}


def _app(seen):
    def discovery(request):
        if str(request.url) == "http://agent/.well-known/mcp-tools.json":
            return httpx.Response(200, json=AGENT_MANIFEST)
        if str(request.url) == "http://agent/openapi.json":
            return httpx.Response(200, json=AGENT_OPENAPI)
        return httpx.Response(404)

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    return create_app("http://gateway.test", transport=httpx.MockTransport(upstream),
                      assembly_env={"ADMIN_API_URL": "http://identity", "AGENT_API_URL": "http://agent"},
                      assembly_transport=httpx.MockTransport(discovery))


def _closed(schema):
    return next(b for b in schema["anyOf"] if b.get("type") == "object")


def test_the_setup_argument_lists_every_allowed_key_and_no_other():
    app = _app([])
    tool = next(t for t in app.state.mcp.tools if t.name == "connection_request")
    setup = _closed(tool.inputSchema["properties"]["setup"])
    assert set(setup["properties"]) == KEYS
    assert setup["additionalProperties"] is False
    assert _closed(setup["properties"]["oauth"])["additionalProperties"] is False
    assert setup["properties"]["fields"]["items"]["additionalProperties"] is False


def test_a_key_outside_it_is_refused_by_name_before_anything_is_forwarded():
    seen = []
    with TestClient(_app(seen)) as client:
        head = {"Authorization": "Bearer k", "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}
        r = client.post("/mcp", headers=head, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}})
        head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        client.post("/mcp", headers=head, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

        def call(setup):
            return client.post("/mcp", headers=head, json={
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "connection_request",
                           "arguments": {"provider": "custom_secret", "label": "Telegram",
                                         "setup": setup}}}).json()["result"]

        refused = call({"endpoint": "https://api.example.test/v1", "service": "Telegram"})
        assert refused["isError"] is True and "'service'" in json.dumps(refused)
        assert seen == []

        ok = call({"endpoint": "https://api.example.test/v1",
                   "fields": [{"name": "chat_id", "label": "Chat ID"}]})
        assert ok["isError"] is False
        assert json.loads(seen[-1].content)["setup"] == {
            "endpoint": "https://api.example.test/v1", "fields": [{"name": "chat_id", "label": "Chat ID"}]}


def test_an_open_object_argument_stays_open():
    """`FlowSubmission.params`-style open objects are not closed by this: only a route's own closed
    model is published as one."""
    from vexa_mcp import register
    assert not register._declares_keys({"type": "object", "additionalProperties": True})
    assert not register._declares_keys({"anyOf": [{"type": "object"}, {"type": "null"}]})
