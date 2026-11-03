"""ONE TRACE THROUGH THE MCP HOP — Vexa-ai/vexa#1783.

The gateway forwards `/mcp` with `X-Trace-Id`; a tool acts by calling back into the gateway. Unless
that call carries the same id, the gateway's line for the tool's call and its line for the `/mcp`
request that caused it are two unrelated traces. Asked at the mounted `/mcp` transport, because the
tool call runs on a second in-process request and that is where the id is easiest to lose.

Every line this service emits must also be a `logevent.v1` line, and none may carry the credential.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient

from vexa_mcp import create_app, obs

REPO = pathlib.Path(__file__).resolve().parents[5]
SCHEMA = json.loads((REPO / "core" / "gateway" / "contracts" / "logevent.v1"
                     / "logevent.schema.json").read_text())
BEARER = "person-secret-key-xyz"
TRACE = "0123456789abcdef0123456789abcdef"


def _validate(line: dict) -> None:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover — the env carries it today
        required = set(SCHEMA["$defs"]["LogEvent"]["required"])
        allowed = set(SCHEMA["$defs"]["LogEvent"]["properties"])
        assert required <= set(line), line
        assert set(line) <= allowed, line
        return
    jsonschema.validate(line, {**SCHEMA["$defs"]["LogEvent"], "$defs": SCHEMA["$defs"]})


@pytest.fixture
def lines():
    sink: list = []
    obs.capture(sink)
    try:
        yield sink
    finally:
        obs.capture(None)


@pytest.fixture
def mcp():
    seen: list = []
    answer = {"status": 200}

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(answer["status"], json={"ok": True})

    app = create_app("http://gateway.test", transport=httpx.MockTransport(gateway))
    ctx = TestClient(app)
    client = ctx.__enter__()

    class Mcp:
        def __init__(self):
            self.seen = seen
            self.answer = answer

        def session(self, trace=None):
            head = {"Authorization": f"Bearer {BEARER}",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json"}
            if trace:
                head["X-Trace-Id"] = trace
            r = client.post("/mcp", headers=head, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}}})
            head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
            client.post("/mcp", headers=head, json={"jsonrpc": "2.0",
                                                    "method": "notifications/initialized"})
            return head

        def call(self, head, name, **arguments):
            return client.post("/mcp", headers=head, json={
                "jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}})

    try:
        yield Mcp()
    finally:
        ctx.__exit__(None, None, None)


def test_inbound_trace_is_echoed_and_on_every_line(mcp, lines):
    head = mcp.session(TRACE)
    r = mcp.call(head, "list_meetings")
    assert r.headers.get("x-trace-id") == TRACE
    assert lines, "no line emitted for a tool call"
    assert {ln["trace_id"] for ln in lines} == {TRACE}
    called = [ln for ln in lines if ln["event"] == "tool_called"]
    assert called and called[-1]["fields"]["tool"] == "list_meetings"
    assert called[-1]["service"] == "mcp"


def test_the_tool_call_to_the_gateway_carries_the_same_trace(mcp, lines):
    head = mcp.session(TRACE)
    mcp.call(head, "list_meetings")
    assert mcp.seen, "the tool never called the gateway"
    assert mcp.seen[-1].headers.get("x-trace-id") == TRACE


def test_a_request_without_a_trace_gets_one_minted_and_carried(mcp, lines):
    head = mcp.session()
    r = mcp.call(head, "list_meetings")
    minted = r.headers.get("x-trace-id")
    assert minted and len(minted) == 32
    # The minted id is THIS request's, and the tool call it caused went out under it.
    assert mcp.seen[-1].headers.get("x-trace-id") == minted
    assert minted in {ln["trace_id"] for ln in lines if ln["event"] == "tool_called"}


def test_every_line_is_a_logevent_v1_line(mcp, lines):
    head = mcp.session(TRACE)
    mcp.call(head, "list_meetings")
    mcp.answer["status"] = 502
    mcp.call(head, "list_meetings")
    assert any(ln["event"] == "upstream_failed" for ln in lines)
    for ln in lines:
        _validate(ln)
        assert "session" not in ln


def test_no_line_carries_the_credential(mcp, lines):
    head = mcp.session(TRACE)
    mcp.call(head, "list_meetings")
    mcp.answer["status"] = 401
    mcp.call(head, "list_meetings")
    assert lines
    assert BEARER not in json.dumps(lines)


from test_assembled_call_path import wired  # noqa: E402,F401 — the assembled path's own fixture


def test_an_assembled_tools_forward_carries_a_trace(wired, lines):
    """The assembled tools reach their domain on a different hop (`register.py`); same rule."""
    wired.call("reactions_list")
    tid = wired.seen[-1].headers.get("x-trace-id")
    assert tid
    assert tid in {ln["trace_id"] for ln in lines if ln["event"] == "tool_called"}
