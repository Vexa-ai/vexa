"""`delete_meeting` works when it is called the way an agent calls it: as an MCP tool.

fastapi-mcp turns a tool call into an HTTP request on this app's own route, and for a DELETE it
sends NO body (`FastApiMCP._request` → `client.delete(path, params=..., headers=...)`). The route
used to read `asked_by_a_human` from a JSON body, so over MCP that argument never arrived: every
call answered 422 and no caller could delete a meeting. Driving the route with `TestClient` and a
JSON body could not see it — these tests go through `/mcp`, so what they assert is what an agent
gets.
"""
from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from vexa_mcp import create_app


def _session(client):
    head = {"Authorization": "Bearer k", "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"}
    r = client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}})
    head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
    client.post("/mcp", headers=head, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return head


def _call(client, head, arguments):
    return client.post("/mcp", headers=head, json={
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "delete_meeting", "arguments": arguments}}).json()["result"]


def _app(seen, status=204, body=None):
    def upstream(request):
        seen.append(request)
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)
    return create_app("http://gateway.test", transport=httpx.MockTransport(upstream))


def test_delete_meeting_over_mcp_reaches_the_meeting_service_with_its_arguments():
    seen = []
    with TestClient(_app(seen)) as client:
        head = _session(client)
        result = _call(client, head, {"meeting_db_id": 4242, "asked_by_a_human": True})
    assert result.get("isError") is not True, json.dumps(result)
    assert [(r.method, r.url.path) for r in seen] == [("DELETE", "/meetings/4242")]
    text = json.dumps(result)
    assert '\\"deleted\\": true' in text and "4242" in text


def test_delete_meeting_over_mcp_is_refused_unless_a_human_asked_and_nothing_is_forwarded():
    seen = []
    with TestClient(_app(seen)) as client:
        head = _session(client)
        for args in ({"meeting_db_id": 4242}, {"meeting_db_id": 4242, "asked_by_a_human": False}):
            result = _call(client, head, args)
            assert result["isError"] is True, args
            if "asked_by_a_human" in args:
                assert "delete_meeting: refused" in json.dumps(result), json.dumps(result)
    assert seen == []


def test_the_tool_publishes_asked_by_a_human_as_a_required_argument():
    app = _app([])
    tool = next(t for t in app.state.mcp.tools if t.name == "delete_meeting")
    assert {"meeting_db_id", "asked_by_a_human"} <= set(tool.inputSchema["required"])
    assert "asked you to delete" in tool.inputSchema["properties"]["asked_by_a_human"]["description"]


def test_the_unwatched_worker_refusal_still_reaches_the_agent_over_mcp():
    seen = []
    refusal = {"code": "person_required", "reason": "needs a person in the loop"}
    with TestClient(_app(seen, status=403, body={"detail": refusal})) as client:
        head = _session(client)
        result = _call(client, head, {"meeting_db_id": 7, "asked_by_a_human": True})
    assert result["isError"] is True
    assert [(r.method, r.url.path) for r in seen] == [("DELETE", "/meetings/7")]
    assert "person_required" in json.dumps(result)


def test_delete_meeting_while_the_bot_finishes_says_it_is_scheduled_not_deleted():
    """A meeting whose bot is still running answers 202: the bot is asked to leave and the meeting is
    deleted when it ends. The tool must not tell the agent it is already deleted."""
    seen = []
    scheduled = {"status": "deletion_scheduled", "id": 4242,
                 "detail": "The bot is leaving this meeting; its transcript and recordings are deleted when it ends."}
    with TestClient(_app(seen, status=202, body=scheduled)) as client:
        head = _session(client)
        result = _call(client, head, {"meeting_db_id": 4242, "asked_by_a_human": True})
    assert result.get("isError") is not True, json.dumps(result)
    text = json.dumps(result)
    assert '\\"deleted\\": false' in text and '\\"scheduled\\": true' in text and "deleted when it ends" in text
