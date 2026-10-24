"""Usage hook — one event per `tools/call`, attributed, signed, and never in the request's way."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import List

import httpx
import pytest
from fastapi.testclient import TestClient

from vexa_mcp import create_app
from vexa_mcp import usage

SECRET = "usage-test-secret"
MEET = "https://meet.google.com/abc-defg-hij"


class Recorder:
    def __init__(self) -> None:
        self.events: List[dict] = []

    def submit(self, event: dict) -> None:
        self.events.append(event)


def _app(sink, gateway=None):
    gateway = gateway or (lambda r: httpx.Response(200, json={"running_bots": []}))
    return create_app("http://gateway.test", transport=httpx.MockTransport(gateway),
                      usage_sink=sink)


class Session:
    def __init__(self, client: TestClient, *, user_id="42", client_name="claude-ai",
                 user_agent="python-httpx/test") -> None:
        self.client = client
        self.head = {"Authorization": "Bearer k",
                     "Accept": "application/json, text/event-stream",
                     "Content-Type": "application/json", "User-Agent": user_agent}
        if user_id is not None:
            self.head["x-user-id"] = user_id
        r = client.post("/mcp", headers=self.head, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": client_name, "version": "1"}}})
        self.head["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        client.post("/mcp", headers=self.head,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def rpc(self, payload):
        return self.client.post("/mcp", headers=self.head, json=payload)

    def call(self, name, **arguments):
        return self.rpc({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                         "params": {"name": name, "arguments": arguments}})


# ── middleware ──────────────────────────────────────────────────────────────────────────────────

def test_initialize_then_tools_call_yields_one_attributed_event():
    rec = Recorder()
    with TestClient(_app(rec)) as client:
        s = Session(client)
        r = s.call("parse_meeting_link", meeting_url=MEET)
        assert r.status_code == 200 and "error" not in r.json()
    assert len(rec.events) == 1
    e = rec.events[0]
    assert e["user_id"] == "42" and e["tool"] == "parse_meeting_link" and e["ok"] is True
    assert e["client_name"] == "claude-ai"
    assert e["user_agent"] == "python-httpx/test"
    assert e["auth"] == "unknown"
    assert e["at"].endswith("+00:00")


def test_non_tool_methods_produce_no_event():
    rec = Recorder()
    with TestClient(_app(rec)) as client:
        s = Session(client)
        s.rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        s.rpc({"jsonrpc": "2.0", "id": 4, "method": "prompts/list"})
        s.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert rec.events == []


def test_no_user_id_no_event_but_still_a_log_line(caplog):
    rec = Recorder()
    with caplog.at_level(logging.INFO, logger="vexa_mcp.usage"):
        with TestClient(_app(rec)) as client:
            Session(client, user_id=None).call("parse_meeting_link", meeting_url=MEET)
    assert rec.events == []
    lines = [r.getMessage() for r in caplog.records if "mcp_tool_call" in r.getMessage()]
    assert lines == ['mcp_tool_call user_id=- tool=parse_meeting_link ok=true client="claude-ai"']


def test_a_refused_tool_call_is_recorded_not_ok():
    rec = Recorder()
    with TestClient(_app(rec, lambda r: httpx.Response(403, json={"detail": "no"}))) as client:
        result = Session(client).call("get_bot_status").json()["result"]
        assert result.get("isError") is True
    assert [e["ok"] for e in rec.events] == [False]


def test_unknown_session_falls_back_to_user_agent():
    """An evicted (or never-seen) session id: the client is named by its User-Agent. A request on a
    session the transport does not know is refused before its body is read, so this drives the
    observer directly."""
    rec = Recorder()
    mw = usage.UsageMiddleware(None, rec)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "list_meetings"}}).encode()
    scope = {"headers": [(b"x-user-id", b"5"), (b"mcp-session-id", b"evicted"),
                         (b"user-agent", b"cursor/1.0")]}
    mw._observe(scope, body, 200, {"content-type": "application/json"},
                b'{"jsonrpc":"2.0","id":1,"result":{"content":[]}}')
    assert [e["client_name"] for e in rec.events] == ["cursor/1.0"]


def test_without_a_sink_the_call_still_works_and_logs(caplog):
    with caplog.at_level(logging.INFO, logger="vexa_mcp.usage"):
        with TestClient(_app(None)) as client:
            r = Session(client).call("parse_meeting_link", meeting_url=MEET)
            assert r.status_code == 200 and r.json()["result"].get("isError") is not True
    assert any("tool=parse_meeting_link ok=true" in r.getMessage() for r in caplog.records)


def test_a_sink_that_raises_does_not_touch_the_response():
    class Boom:
        def submit(self, event):
            raise RuntimeError("sink down")

    with TestClient(_app(Boom())) as client:
        r = Session(client).call("parse_meeting_link", meeting_url=MEET)
    assert r.status_code == 200
    assert r.json()["result"].get("isError") is not True


def test_the_session_lru_is_bounded():
    lru = usage._LRU(3)
    for i in range(5):
        lru.set(f"s{i}", f"c{i}")
    assert len(lru) == 3 and lru.get("s0") is None and lru.get("s4") == "c4"


def test_a_batch_request_records_each_call():
    rec = Recorder()
    mw = usage.UsageMiddleware(None, rec)
    body = json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "a"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "b"}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ]).encode()
    reply = json.dumps([
        {"jsonrpc": "2.0", "id": 1, "result": {"content": []}},
        {"jsonrpc": "2.0", "id": 2, "result": {"content": [], "isError": True}},
        {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}},
    ]).encode()
    scope = {"headers": [(b"x-user-id", b"9")]}
    mw._observe(scope, body, 200, {"content-type": "application/json"}, reply)
    assert [(e["tool"], e["ok"]) for e in rec.events] == [("a", True), ("b", False)]


def _asgi_scope(method, path, headers):
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
            "path": path, "raw_path": path.encode(), "root_path": "", "scheme": "http",
            "query_string": b"", "client": ("127.0.0.1", 1), "server": ("t", 80),
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}


async def test_the_sse_get_and_other_routes_pass_through_untouched():
    """The middleware wraps ONLY POST /mcp: a GET (the SSE stream) reaches the app with the very
    same receive/send callables, so nothing is buffered or re-timed."""
    seen = []

    async def inner(scope, receive, send):
        seen.append((scope["method"], scope["path"], receive, send))

    rec = Recorder()
    mw = usage.UsageMiddleware(inner, rec)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    await mw(_asgi_scope("GET", "/mcp", {"accept": "text/event-stream"}), receive, send)
    await mw(_asgi_scope("DELETE", "/mcp", {}), receive, send)
    await mw(_asgi_scope("POST", "/bot-status", {}), receive, send)
    assert [(m, p) for m, p, *_ in seen] == [("GET", "/mcp"), ("DELETE", "/mcp"),
                                              ("POST", "/bot-status")]
    assert all(r is receive and s is send for *_, r, s in seen)
    assert rec.events == []


async def test_an_oversized_body_is_not_inspected_and_still_forwarded():
    got = bytearray()

    async def inner(scope, receive, send):
        while True:
            msg = await receive()
            got.extend(msg.get("body") or b"")
            if not msg.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})

    rec = Recorder()
    mw = usage.UsageMiddleware(inner, rec)
    big = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x","pad":"' + \
        b"a" * (usage.MAX_BODY + 10) + b'"}}'
    chunks = [big[: len(big) // 2], big[len(big) // 2:]]

    async def receive():
        c = chunks.pop(0)
        return {"type": "http.request", "body": c, "more_body": bool(chunks)}

    sent = []

    async def send(m):
        sent.append(m)

    await mw(_asgi_scope("POST", "/mcp", {"x-user-id": "1"}), receive, send)
    assert bytes(got) == big
    assert sent[0]["status"] == 200
    assert rec.events == []


# ── sink ────────────────────────────────────────────────────────────────────────────────────────

def _event(i=0):
    return {"user_id": "1", "tool": f"t{i}", "ok": True, "client_name": "c", "user_agent": "u",
            "auth": "unknown", "at": "2026-10-21T00:00:00+00:00"}


def test_the_sink_signs_each_batch_the_way_the_webapp_verifies():
    posted = []

    def collector(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(200, json={"ok": True})

    sink = usage.UsageSink("http://webapp/api/internal/mcp/usage", SECRET,
                           transport=httpx.MockTransport(collector), start=False)
    sink.submit(_event(1))
    sink.submit(_event(2))
    assert sink.flush() == 2
    assert len(posted) == 1
    req = posted[0]
    raw = req.content
    ts = req.headers["x-webhook-timestamp"]
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    assert abs(int(ts) - int(time.time())) < 5
    expected = hmac.new(SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    assert req.headers["x-webhook-signature"] == f"sha256={expected}"
    assert json.loads(raw) == {"events": [_event(1), _event(2)]}


def test_the_sink_batches_at_flush_at():
    posted = []
    sink = usage.UsageSink("http://c", SECRET, flush_at=50, start=False,
                           transport=httpx.MockTransport(
                               lambda r: posted.append(json.loads(r.content)) or httpx.Response(204)))
    for i in range(120):
        sink.submit(_event(i))
    assert sink.flush() == 120
    assert [len(p["events"]) for p in posted] == [50, 50, 20]


def test_a_failing_sink_never_raises_and_counts_failures():
    def down(request):
        raise httpx.ConnectError("refused")

    sink = usage.UsageSink("http://c", SECRET, transport=httpx.MockTransport(down), start=False)
    sink.submit(_event())
    assert sink.flush() == 0
    assert sink.failed_batches == 1

    sink5 = usage.UsageSink("http://c", SECRET, start=False,
                            transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    sink5.submit(_event())
    assert sink5.flush() == 0 and sink5.failed_batches == 1


def test_overflow_drops_and_counts():
    sink = usage.UsageSink("http://c", SECRET, queue_max=3, start=False,
                           transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    for i in range(5):
        sink.submit(_event(i))
    assert sink.dropped == 2
    assert sink.flush() == 3


def test_the_background_thread_flushes_on_its_own():
    posted = []
    sink = usage.UsageSink("http://c", SECRET, flush_every=0.05,
                           transport=httpx.MockTransport(
                               lambda r: posted.append(r) or httpx.Response(200)))
    sink.submit(_event())
    deadline = time.time() + 2
    while not posted and time.time() < deadline:
        time.sleep(0.02)
    assert posted


@pytest.mark.parametrize("env,on", [
    ({}, False),
    ({"MCP_USAGE_URL": "http://c"}, False),
    ({"MCP_USAGE_SECRET": "s"}, False),
    ({"MCP_USAGE_URL": "http://c", "MCP_USAGE_SECRET": "s"}, True),
])
def test_the_sink_is_off_unless_both_keys_are_set(env, on):
    sink = usage.UsageSink.from_env(env)
    assert (sink is not None) is on


def test_the_secret_never_reaches_a_log_line(caplog):
    canary = "canary-secret-7f3a"
    with caplog.at_level(logging.DEBUG):
        usage.UsageSink.from_env({"MCP_USAGE_SECRET": canary})
        sink = usage.UsageSink("http://c", canary, start=False,
                               transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        sink.submit(_event())
        sink.flush()
    assert caplog.records, "the half-configured sink and the failed batch must both be logged"
    assert not any(canary in r.getMessage() for r in caplog.records)


def test_end_to_end_through_a_real_sink():
    posted = []
    sink = usage.UsageSink("http://c", SECRET, start=False,
                           transport=httpx.MockTransport(
                               lambda r: posted.append(json.loads(r.content)) or httpx.Response(200)))
    with TestClient(_app(sink)) as client:
        Session(client, client_name="ChatGPT").call("parse_meeting_link", meeting_url=MEET)
    sink.flush()
    (ev,) = posted[0]["events"]
    assert (ev["user_id"], ev["tool"], ev["ok"], ev["client_name"]) == \
        ("42", "parse_meeting_link", True, "ChatGPT")
