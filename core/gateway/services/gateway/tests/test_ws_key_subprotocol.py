"""The websocket key travels in a header or the subprotocol list, never in the URL (ws.v1 § Connect).

A browser `WebSocket` cannot set `x-api-key`, so it names `["vexa.v1", "vexa.key.<key>"]` and the
edge answers `vexa.v1` — the key is in `Sec-WebSocket-Protocol`, which no access log prints. The old
`?api_key=` still connects (published clients use it) and each use is logged as deprecated.
"""
from __future__ import annotations

from typing import Optional

from gateway import multiplex as mx
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


async def test_the_query_key_still_connects_and_is_logged_deprecated(monkeypatch):
    events = []
    monkeypatch.setattr(mx, "log_event", lambda name, **kw: events.append((name, kw)))
    ws = _ProtoWS(query=API_KEY)
    await _run_multiplex(ws, _auth(), FakeRedis())
    assert {"type": "pong"} in ws.sent
    deprecated = [kw for name, kw in events if name == "ws_query_key_deprecated"]
    assert deprecated and API_KEY not in repr(deprecated)


def test_ws_v1_entries_never_travel_upstream():
    assert without_vexa(["binary", SUBPROTOCOL, f"vexa.key.{API_KEY}"]) == ["binary"]


def test_the_order_is_header_then_subprotocol_then_query():
    ws = _ProtoWS(api_key="from-header", subprotocols=["vexa.key.from-proto"], query="from-query")
    assert ws_credential(ws).key == "from-header"
    ws = _ProtoWS(subprotocols=["vexa.key.from-proto"], query="from-query")
    assert ws_credential(ws)[::2] == ("from-proto", "subprotocol")
    ws = _ProtoWS(query="from-query")
    assert ws_credential(ws)[::2] == ("from-query", "query")
