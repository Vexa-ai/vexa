"""The websocket key travels in a header or the subprotocol list, never in the URL (ws.v1 § Connect).

A browser `WebSocket` cannot set `x-api-key`, so it names `["vexa.v1", "vexa.key.<key>"]` and the
edge answers `vexa.v1` — the key is in `Sec-WebSocket-Protocol`, which no access log prints. A key in
the URL (`?api_key=`) is refused with `api_key_query_removed` and close 4401, whatever else the socket
carries, and the frame says what to do instead and where to read.
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


QUERY_REFUSAL = {
    "type": "error", "error": "api_key_query_removed", "changed_in": "0.13.4",
    "do_instead": ("send the key in the x-api-key header, or (a browser) in the subprotocol list: "
                   "new WebSocket(url, ['vexa.v1', 'vexa.key.' + key])"),
    "docs": "https://docs.vexa.ai/changes-0-13-4-auth",
}


async def test_a_key_in_the_url_is_refused_with_what_to_do_instead(monkeypatch):
    events = []
    monkeypatch.setattr(mx, "log_event", lambda name, **kw: events.append((name, kw)))
    ws = _ProtoWS(query=API_KEY)
    await _run_multiplex(ws, _auth(), FakeRedis())
    assert ws.sent == [QUERY_REFUSAL] and ws.close_code == 4401
    refused = [kw for name, kw in events if name == "ws_query_key_refused"]
    assert refused and API_KEY not in repr(refused)


async def test_a_key_in_the_url_is_refused_even_beside_a_valid_header_or_subprotocol():
    for ws in (_ProtoWS(query=API_KEY, api_key=API_KEY),
               _ProtoWS(query="x", subprotocols=[SUBPROTOCOL, f"vexa.key.{API_KEY}"])):
        await _run_multiplex(ws, _auth(), FakeRedis())
        assert ws.sent == [QUERY_REFUSAL] and ws.close_code == 4401
        assert {"type": "pong"} not in ws.sent


def test_ws_v1_entries_never_travel_upstream():
    assert without_vexa(["binary", SUBPROTOCOL, f"vexa.key.{API_KEY}"]) == ["binary"]


def test_the_order_is_header_then_subprotocol_and_the_url_is_never_a_credential():
    ws = _ProtoWS(api_key="from-header", subprotocols=["vexa.key.from-proto"])
    assert ws_credential(ws)[::2] == ("from-header", "header")
    ws = _ProtoWS(subprotocols=["vexa.key.from-proto"])
    assert ws_credential(ws)[::2] == ("from-proto", "subprotocol")
    ws = _ProtoWS(api_key="from-header", subprotocols=["vexa.key.from-proto"], query="from-query")
    assert ws_credential(ws)[::2] == (None, "query")
