"""The websocket key travels in a header or the subprotocol list (ws.v1 § Connect).

A browser `WebSocket` cannot set `x-api-key`, so it names `["vexa.v1", "vexa.key.<key>"]` and the
edge answers `vexa.v1` — the key is in `Sec-WebSocket-Protocol`, which no access log prints. A key in
the URL (`?api_key=`) is DEPRECATED: still a credential so 0.12 clients keep working, taken last,
logged `ws_query_key_deprecated` with a running count and never the key, until COMPAT_REMOVED_IN.
"""
from __future__ import annotations

from typing import Optional

from gateway import changed, multiplex as mx
from gateway.multiplex import _run_multiplex
from gateway.ws_auth import SUBPROTOCOL, without_vexa, ws_credential
from conftest import FakeAuthorizer, FakeRedis
from test_multiplex import API_KEY, AUTH_MAP, _WS


class _ProtoWS(_WS):
    def __init__(self, *, subprotocols=None, query: Optional[str] = None, **kw):
        super().__init__(inbound=[{"action": "ping"}], **kw)
        self.scope = {"subprotocols": list(subprotocols or [])}
        if query:
            self.query_params = {"api_key": query}
        self.accepted_with: Optional[str] = "unset"

    async def accept(self, subprotocol: Optional[str] = None) -> None:
        self.accepted_with = subprotocol


def _auth():
    return FakeAuthorizer(valid_key=API_KEY, auth_map=AUTH_MAP)


async def test_a_browser_key_in_the_subprotocol_list_connects_and_is_answered_vexa_v1():
    ws = _ProtoWS(subprotocols=[SUBPROTOCOL, f"vexa.key.{API_KEY}"])
    await _run_multiplex(ws, _auth(), FakeRedis())
    assert {"type": "pong"} in ws.sent and ws.close_code is None
    # the answer names ws.v1, never the entry that carries the key
    assert ws.accepted_with == SUBPROTOCOL


async def test_a_wrong_key_in_the_subprotocol_list_is_refused_4401():
    ws = _ProtoWS(subprotocols=[SUBPROTOCOL, "vexa.key.vxa_bot_nobody"])
    await _run_multiplex(ws, _auth(), FakeRedis())
    assert ws.close_code == 4401 and ws.sent[0]["error"] == "invalid_api_key"


async def test_the_header_wins_and_nothing_is_answered_when_nothing_was_offered():
    ws = _ProtoWS(api_key=API_KEY)
    await _run_multiplex(ws, _auth(), FakeRedis())
    assert {"type": "pong"} in ws.sent and ws.accepted_with is None


def _deprecated(events):
    return [kw["fields"] for name, kw in events if name == "ws_query_key_deprecated"]


async def test_a_key_in_the_url_still_connects_and_each_use_is_counted(monkeypatch):
    """0.12 clients (the hosted dashboard, ASWF DNA, clients built from our docs) keep working."""
    events = []
    monkeypatch.setattr(mx, "log_event", lambda name, **kw: events.append((name, kw)))
    monkeypatch.setattr(changed, "COMPAT_USE", {})
    for n in (1, 2):
        ws = _ProtoWS(query=API_KEY)
        await _run_multiplex(ws, _auth(), FakeRedis())
        assert {"type": "pong"} in ws.sent and ws.close_code is None, ws.sent
        assert not [f for f in ws.sent if f.get("type") == "error"]
        logged = _deprecated(events)
        assert len(logged) == n and logged[-1]["count"] == n
    assert logged[-1]["removed_in"] == changed.COMPAT_REMOVED_IN == "0.13.6"
    assert API_KEY not in repr(events)               # the key itself is never in a log line


async def test_a_wrong_key_in_the_url_is_refused_4401_like_any_wrong_key():
    ws = _ProtoWS(query="vxa_bot_nobody")
    await _run_multiplex(ws, _auth(), FakeRedis())
    assert ws.close_code == 4401 and ws.sent[0]["error"] == "invalid_api_key"


async def test_a_header_or_subprotocol_key_wins_over_the_url_and_is_not_counted(monkeypatch):
    events = []
    monkeypatch.setattr(mx, "log_event", lambda name, **kw: events.append((name, kw)))
    for ws in (_ProtoWS(query="vxa_bot_nobody", api_key=API_KEY),
               _ProtoWS(query="vxa_bot_nobody", subprotocols=[SUBPROTOCOL, f"vexa.key.{API_KEY}"])):
        await _run_multiplex(ws, _auth(), FakeRedis())
        assert {"type": "pong"} in ws.sent and ws.close_code is None
    assert not _deprecated(events)


def test_ws_v1_entries_never_travel_upstream():
    assert without_vexa(["binary", SUBPROTOCOL, f"vexa.key.{API_KEY}"]) == ["binary"]


def test_the_order_is_header_then_subprotocol_then_the_deprecated_url():
    ws = _ProtoWS(api_key="from-header", subprotocols=["vexa.key.from-proto"], query="from-query")
    assert ws_credential(ws)[::2] == ("from-header", "header")
    ws = _ProtoWS(subprotocols=["vexa.key.from-proto"], query="from-query")
    assert ws_credential(ws)[::2] == ("from-proto", "subprotocol")
    ws = _ProtoWS(query="from-query")
    assert ws_credential(ws)[::2] == ("from-query", "query")
