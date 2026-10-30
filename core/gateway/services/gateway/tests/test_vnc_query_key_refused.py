"""The bot-account live view refuses a key in the URL WHATEVER ELSE the request carries (fix #5).

`/ws` takes `?api_key=` again for 0.12 clients until 0.13.6, and `ws_auth.ws_credential` reads the
header and the subprotocol first. The VNC relay must not inherit that: a request to
`/bot-accounts/sessions/{id}/vnc?api_key=…` is refused with `api_key_query_removed` and close 4401,
even when it also carries a valid key in the header or the subprotocol list, and nothing is opened
upstream.
"""
from __future__ import annotations

from conftest import VALID_KEY
from test_bot_accounts import VNC, FakeConnector, _client


def _refused(ws) -> dict:
    frame = ws.receive_json()
    assert ws.receive()["code"] == 4401
    return frame


def test_a_url_key_alongside_a_header_key_is_refused():
    connector = FakeConnector()
    client, _ = _client(connector)
    with client.websocket_connect(f"{VNC}?api_key={VALID_KEY}", headers={"x-api-key": VALID_KEY}) as ws:
        frame = _refused(ws)
    assert frame["error"] == "api_key_query_removed"
    assert connector.calls == []


def test_a_url_key_alongside_a_subprotocol_key_is_refused():
    connector = FakeConnector()
    client, _ = _client(connector)
    with client.websocket_connect(f"{VNC}?api_key={VALID_KEY}",
                                  subprotocols=["vexa.v1", f"vexa.key.{VALID_KEY}"]) as ws:
        frame = _refused(ws)
    assert frame["error"] == "api_key_query_removed"
    assert connector.calls == []


def test_an_empty_url_key_alongside_a_header_key_is_refused():
    connector = FakeConnector()
    client, _ = _client(connector)
    with client.websocket_connect(f"{VNC}?api_key=", headers={"x-api-key": VALID_KEY}) as ws:
        frame = _refused(ws)
    assert frame["error"] == "api_key_query_removed"
    assert connector.calls == []
