"""An array or object argument that arrives as its JSON text is read as the value it spells.

On 2026-10-13 an agent called `entity_upsert(kind="meeting", facts=["…"], fields={…}, dates={…})`
and the edge answered `Input validation error: '["…"]' is not of type 'array'` every time: the
model had serialised `facts` into a string, the MCP's own schema check refused it before anything
reached the route, and the agent could not see the difference between what it sent and what it
meant (Vexa-ai/vexa#1783). The same call with fewer nested arguments went through.

What has to be true: a string that is the JSON of an array (for an array argument) or of an object
(for an object argument) is decoded before the schema check; a bare string for an array of strings
is one item, which is the owning route's own rule (`EntityUpsertBody._one_is_a_list`); anything
else is still refused by the schema, unchanged.
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


def _session(client):
    head = {"Authorization": "Bearer k", "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"}
    r = client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}})
    head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
    client.post("/mcp", headers=head, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(arguments):
        return client.post("/mcp", headers=head, json={
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "entity_upsert", "arguments": arguments}}).json()["result"]
    return call


def test_json_text_for_an_array_and_for_objects_is_read_as_the_value():
    seen = []
    with TestClient(_app(seen)) as client:
        call = _session(client)
        ok = call({"kind": "meeting", "name": "Weekly sync", "source": "the call",
                   "facts": json.dumps(["They agreed to ship on Friday."]),
                   "fields": json.dumps({"when": "2026-10-13", "who": "Ada Lovelace"}),
                   "dates": json.dumps({"held_at": "2026-10-13T10:00:00Z"})})
        assert ok["isError"] is False, ok
        sent = json.loads(seen[-1].content)
        assert sent["facts"] == ["They agreed to ship on Friday."]
        assert sent["fields"] == {"when": "2026-10-13", "who": "Ada Lovelace"}
        assert sent["dates"] == {"held_at": "2026-10-13T10:00:00Z"}


def test_a_bare_string_for_a_list_of_strings_is_one_item():
    seen = []
    with TestClient(_app(seen)) as client:
        ok = _session(client)({"kind": "project", "name": "Launch", "source": "the call",
                               "facts": "It ships on Friday."})
        assert ok["isError"] is False, ok
        assert json.loads(seen[-1].content)["facts"] == ["It ships on Friday."]


def test_a_value_of_the_wrong_kind_is_still_refused():
    seen = []
    with TestClient(_app(seen)) as client:
        refused = _session(client)({"kind": "meeting", "name": "Weekly sync",
                                    "dates": json.dumps(["not", "an", "object"])})
        assert refused["isError"] is True
        assert seen == []
