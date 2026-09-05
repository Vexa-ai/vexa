"""An upstream error must reach the agent as STRUCTURE, not as prose with JSON inside it.

`test_denial_reaches_the_agent` pins that the refusal's words survive the hop. This file pins the
SHAPE they arrive in, which is what decides whether an agent can act on them without writing a parser:

    <reason>: <message>
    action_url: …
    {"code":"…","reason":"…","decision_id":"…","message":"…","action_url":"…"}

and, when the decider authored no message, a first line that still says something — `HTTP <status>
<code>` — rather than an empty one.

The second half of the file pins the envelope depth. `{"detail": {"detail": {…}}}` is not a wire
format anybody designed: it is what happens when a service re-raises an upstream body as its own
`HTTPException(detail=…)`. Unwrapping is depth-insensitive on purpose, so adding or removing a hop
cannot change what a caller has to reach through.

No vocabulary of ours is asserted here beyond the generic `code` the API already publishes: the
fixture's reason is a string this build has never heard of, and it must render exactly as well.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vexa_mcp import create_app
from vexa_mcp.tool_errors import render_tool_error, unwrap_detail
from conftest import API_KEY, GATEWAY_URL

REASON = "a_reason_this_build_has_never_heard_of"
MESSAGE = "This account cannot start bots right now. Open the account page to fix it."
ACTION_URL = "https://example.invalid/account"

FULL = {
    "code": "service_not_allowed",
    "reason": REASON,
    "decision_id": "decision-fixture-77",
    "message": MESSAGE,
    "action_url": ACTION_URL,
}
BARE = {k: FULL[k] for k in ("code", "reason", "decision_id")}

MEETING_URL = "https://meet.google.com/abc-defg-hij"


# ── the renderer, on its own ────────────────────────────────────────────────────────────────────

def test_the_three_lines():
    lines = render_tool_error(403, json.dumps({"detail": FULL})).splitlines()
    assert lines[0] == f"{REASON}: {MESSAGE}"
    assert lines[1] == f"action_url: {ACTION_URL}"
    assert json.loads(lines[2]) == FULL
    assert len(lines) == 3


def test_no_message_still_says_what_happened():
    lines = render_tool_error(403, json.dumps({"detail": BARE})).splitlines()
    assert lines[0] == "HTTP 403 service_not_allowed"
    assert not any(line.startswith("action_url:") for line in lines)
    assert json.loads(lines[-1]) == BARE


def test_the_body_appears_once():
    """The library's rendering repeated the payload inside a sentence; this one does not repeat it."""
    rendered = render_tool_error(403, json.dumps({"detail": FULL}))
    assert rendered.count('"decision_id"') == 1


def test_a_body_that_is_not_json_is_still_shown():
    rendered = render_tool_error(502, "<html>Bad Gateway</html>")
    assert rendered.splitlines() == ["HTTP 502", "<html>Bad Gateway</html>"]


def test_an_empty_body_renders_the_status_alone():
    assert render_tool_error(500, "") == "HTTP 500"


# ── the envelope ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("depth", [0, 1, 2, 3])
def test_any_depth_of_detail_unwraps_to_the_same_object(depth):
    body = FULL
    for _ in range(depth):
        body = {"detail": body}
    assert unwrap_detail(body) == FULL


def test_a_detail_with_siblings_is_left_alone():
    """Peeling a one-key envelope is safe; peeling past siblings would DELETE fields."""
    body = {"detail": FULL, "trace_id": "t-1"}
    assert unwrap_detail(body) == body


def test_triple_nesting_renders_the_same_three_lines():
    rendered = render_tool_error(403, json.dumps({"detail": {"detail": {"detail": FULL}}}))
    assert rendered.splitlines()[0] == f"{REASON}: {MESSAGE}"
    assert json.loads(rendered.splitlines()[2]) == FULL


# ── what the agent actually reads over /mcp ─────────────────────────────────────────────────────

def _refusing_app(detail):
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bots" and request.method == "POST":
            return httpx.Response(403, json={"detail": detail})
        return httpx.Response(200, json={"ok": True})

    return create_app(GATEWAY_URL, transport=httpx.MockTransport(upstream))


def _call(detail):
    with TestClient(_refusing_app(detail)) as client:
        head = {
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        r = client.post("/mcp", headers=head, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}})
        head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        client.post("/mcp", headers=head,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        got = client.post("/mcp", headers=head, json={
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "request_meeting_bot",
                       "arguments": {"meeting_url": MEETING_URL}}})
        return got.json()["result"]


def test_the_agent_reads_the_three_lines_and_it_is_an_error():
    result = _call(FULL)
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    lines = text.splitlines()
    assert lines[0] == f"{REASON}: {MESSAGE}", text
    assert lines[1] == f"action_url: {ACTION_URL}", text
    assert json.loads(lines[2]) == FULL, text


def test_the_agent_is_not_handed_a_sentence_with_json_in_it():
    """The failure this replaces: prose the agent has to parse a document out of."""
    text = _call(FULL)["content"][0]["text"]
    assert "Status code:" not in text
    assert '{"detail"' not in text


def test_a_message_less_refusal_still_names_the_code():
    text = _call(BARE)["content"][0]["text"]
    assert text.splitlines()[0] == "HTTP 403 service_not_allowed", text
