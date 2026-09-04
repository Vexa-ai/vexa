"""When a bot is refused, the AGENT must be told enough to do something about it.

`request_meeting_bot` is the tool a person's agent calls, so it is the surface where a refusal
either becomes actionable or becomes a dead end. Upstream answers
`403 {"detail": {"code", "reason", "decision_id", "message"?, "action_url"?}}`; the last two are
authored by whichever service decided, are optional, and are the only part of that body a human or
an agent can act on. A `reason` slug alone tells an agent that something is closed and nothing
about what would open it.

Both halves are pinned here because they fail differently and silently:

  * the **forwarding route** (`make_request`) must carry the upstream JSON body into the raised
    error rather than flattening it to a status line, and
  * the **mounted `/mcp` transport** — what the agent actually reads — must surface those fields in
    the tool result's text, marked `isError`.

The second is the one nothing else covers. `fastapi-mcp` renders a failed tool call by embedding
the response body in an error string, so the fields ride along today for free — which is exactly
why it needs a test: an error-envelope change in that library, or a `detail`-to-string tidy-up in
this service, would drop the words with every existing test still green.

Fixture-local strings only: no plan, price, currency or URL of ours appears here.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vexa_mcp import create_app
from conftest import API_KEY, GATEWAY_URL

REASON = "a_reason_this_build_has_never_heard_of"
MESSAGE = "This account cannot start bots right now. Open the account page to fix it."
ACTION_URL = "https://example.invalid/account"

DENIAL = {
    "code": "service_not_allowed",
    "reason": REASON,
    "decision_id": "decision-fixture-77",
    "message": MESSAGE,
    "action_url": ACTION_URL,
}

MEETING_URL = "https://meet.google.com/abc-defg-hij"


def _refusing_app(detail):
    """An MCP app whose gateway refuses `POST /bots` with `detail`."""

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bots" and request.method == "POST":
            return httpx.Response(403, json={"detail": detail})
        return httpx.Response(200, json={"ok": True})

    return create_app(GATEWAY_URL, transport=httpx.MockTransport(upstream))


# ── the forwarding route: the body survives the hop ─────────────────────────────────────────────

def test_the_route_carries_the_whole_denial_body():
    client = TestClient(_refusing_app(DENIAL))
    r = client.post(
        "/request-meeting-bot",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"meeting_url": MEETING_URL},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == DENIAL


def test_the_route_adds_nothing_when_the_decider_said_nothing():
    bare = {k: DENIAL[k] for k in ("code", "reason", "decision_id")}
    client = TestClient(_refusing_app(bare))
    r = client.post(
        "/request-meeting-bot",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"meeting_url": MEETING_URL},
    )
    body = r.json()["detail"]["detail"]
    assert body == bare
    assert "message" not in body and "action_url" not in body


# ── the mounted transport: what the agent reads ─────────────────────────────────────────────────

@pytest.fixture
def agent():
    """A live `/mcp` session against an app whose gateway refuses the spawn."""
    ctx = TestClient(_refusing_app(DENIAL))
    client = ctx.__enter__()
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

    def call(name, **arguments):
        got = client.post("/mcp", headers=head, json={
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})
        return got.json()["result"]

    try:
        yield call
    finally:
        ctx.__exit__(None, None, None)


def test_the_tool_result_is_marked_an_error(agent):
    """A refusal that arrives success-shaped is worse than one that arrives at all: the agent
    proceeds as though a bot were on its way."""
    assert agent("request_meeting_bot", meeting_url=MEETING_URL).get("isError") is True


def test_the_tool_result_carries_reason_message_and_action_url(agent):
    """The three fields, in the text an agent reads. This is the whole point of the change."""
    text = json.dumps(agent("request_meeting_bot", meeting_url=MEETING_URL))
    assert REASON in text, text
    assert MESSAGE in text, text
    assert ACTION_URL in text, text
