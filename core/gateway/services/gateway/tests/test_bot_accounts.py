"""Bot accounts at the edge (ADR-0049): the REST rows forward like /bots, the live-view files come
back as bytes with their content-type, and the sign-in session's websocket is authenticated as /ws
is, stamped with the identity an HTTP forward carries, and relayed to meeting-api frame by frame.

The upstream websocket is a fake connector injected through ``create_app(vnc_connect=...)``: the
assertions are on what the edge refuses, what it sends upstream, and what it relays.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from fastapi.testclient import TestClient

from gateway import create_app, identity_token
from gateway.vnc_relay import UpstreamRefused, upstream_url
from conftest import (SIGNING_KEY, VALID_KEY, VALID_USER, VERIFY_KEY, FakeAuthorizer,
                      FakeDownstream, FakeRedis)

SID = "0123456789abcdef0123456789abcdef"
VNC = f"/bot-accounts/sessions/{SID}/vnc"
GREETING = b"RFB 003.008\n"


class FakeUpstream:
    """meeting-api's end of the socket: greets, echoes every frame, and closes on b"bye"."""

    def __init__(self, close_code: int = 1000):
        self.sent: list = []
        self.subprotocol = None
        self.close_code = None
        self._final = close_code
        self._q: asyncio.Queue = asyncio.Queue()
        self._q.put_nowait(GREETING)

    async def send(self, data):
        self.sent.append(data)
        await self._q.put(None if data == b"bye" else data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        frame = await self._q.get()
        if frame is None:
            self.close_code = self._final
            raise StopAsyncIteration
        return frame


class FakeConnector:
    def __init__(self, upstream=None, refuse: int | None = None):
        self.upstream = upstream or FakeUpstream()
        self.refuse = refuse
        self.calls: list = []

    def __call__(self, url, headers, subprotocols):
        self.calls.append({"url": url, "headers": dict(headers), "subprotocols": subprotocols})

        @contextlib.asynccontextmanager
        async def opened():
            if self.refuse is not None:
                raise UpstreamRefused(self.refuse)
            yield self.upstream
        return opened()


def _client(connector=None, user=None, **kw):
    downstream = kw.pop("downstream", None) or FakeDownstream()
    app = create_app(FakeAuthorizer(user=user), downstream, FakeRedis(),
                     identity_key=SIGNING_KEY, vnc_connect=connector or FakeConnector(), **kw)
    return TestClient(app), downstream


def _closed_with(client, path, headers=None) -> int:
    """The close code the edge answers with (the error frame before it is skipped)."""
    with client.websocket_connect(path, headers=headers or {}) as ws:
        while True:
            message = ws.receive()
            if message["type"] == "websocket.close":
                return message["code"]


# ── refusals ────────────────────────────────────────────────────────────────────────────────────

def test_a_socket_with_no_key_is_closed_4401_and_nothing_is_opened_upstream():
    connector = FakeConnector()
    client, _ = _client(connector)
    assert _closed_with(client, VNC) == 4401
    assert connector.calls == []


def test_a_key_this_edge_does_not_accept_is_closed_4401():
    connector = FakeConnector()
    client, _ = _client(connector)
    assert _closed_with(client, VNC, {"x-api-key": "not-a-key"}) == 4401
    assert connector.calls == []


def test_a_key_without_the_bot_scope_is_closed_4403():
    connector = FakeConnector()
    client, _ = _client(connector, user={**VALID_USER, "scopes": ["tx", "browser"]})
    assert _closed_with(client, VNC, {"x-api-key": VALID_KEY}) == 4403
    assert connector.calls == []


def test_a_session_id_that_is_not_32_hex_is_closed_4404():
    connector = FakeConnector()
    client, _ = _client(connector)
    assert _closed_with(client, "/bot-accounts/sessions/NOTHEX/vnc", {"x-api-key": VALID_KEY}) == 4404
    assert connector.calls == []


@pytest.mark.parametrize("status,code", [(404, 4404), (403, 4403), (401, 4401), (502, 1011)])
def test_an_upstream_refusal_reaches_the_client_as_a_close_code(status, code):
    client, _ = _client(FakeConnector(refuse=status))
    assert _closed_with(client, VNC, {"x-api-key": VALID_KEY}) == code


# ── the relay ───────────────────────────────────────────────────────────────────────────────────

def test_frames_are_relayed_both_ways_verbatim():
    connector = FakeConnector()
    client, _ = _client(connector)
    with client.websocket_connect(VNC, headers={"x-api-key": VALID_KEY}) as ws:
        assert ws.receive_bytes() == GREETING
        ws.send_bytes(b"\x00\x01\xffframe")
        assert ws.receive_bytes() == b"\x00\x01\xffframe"
        ws.send_text("text stays text")
        assert ws.receive_text() == "text stays text"
    assert connector.upstream.sent[:2] == [b"\x00\x01\xffframe", "text stays text"]


def test_the_upstream_closing_closes_the_client_with_its_code():
    client, _ = _client(FakeConnector(FakeUpstream(close_code=4404)))
    with client.websocket_connect(VNC, headers={"x-api-key": VALID_KEY}) as ws:
        assert ws.receive_bytes() == GREETING
        ws.send_bytes(b"bye")
        message = ws.receive()
    assert message == {"type": "websocket.close", "code": 4404, "reason": ""}


def test_the_upstream_is_meeting_api_at_the_same_path_with_the_signed_identity():
    connector = FakeConnector()
    client, _ = _client(connector, meeting_api_url="http://meeting-api:8080")
    spoof = {"x-api-key": VALID_KEY, "x-user-id": "999", "x-vexa-identity": "forged",
             "x-internal-secret": "nope"}
    with client.websocket_connect(VNC, headers=spoof) as ws:
        assert ws.receive_bytes() == GREETING
    call = connector.calls[0]
    assert call["url"] == f"ws://meeting-api:8080{VNC}"
    headers = call["headers"]
    claims = identity_token.verify(VERIFY_KEY, headers[identity_token.HEADER])
    assert claims["sub"] == str(VALID_USER["user_id"])
    assert headers["x-user-id"] == str(VALID_USER["user_id"])
    assert "x-api-key" not in headers and "authorization" not in headers   # the key stays at the edge
    assert "x-internal-secret" not in headers


def test_a_key_in_the_query_string_is_refused_as_on_ws():
    connector = FakeConnector()
    client, _ = _client(connector)
    with client.websocket_connect(f"{VNC}?api_key={VALID_KEY}") as ws:
        frame = ws.receive_json()
        assert ws.receive()["code"] == 4401
    assert frame["error"] == "api_key_query_removed" and frame["changed_in"] == "0.13.4"
    assert frame["docs"] == "https://docs.vexa.ai/changes-0-13-4-auth" and "vexa.key." in frame["do_instead"]
    assert connector.calls == []


def test_the_key_may_ride_the_subprotocol_list():
    connector = FakeConnector()
    client, _ = _client(connector)
    with client.websocket_connect(VNC, subprotocols=["vexa.v1", f"vexa.key.{VALID_KEY}"]) as ws:
        assert ws.receive_bytes() == GREETING
    assert len(connector.calls) == 1


def test_the_upstream_url_takes_the_websocket_scheme():
    assert upstream_url("https://m.example/", SID) == f"wss://m.example{VNC}"
    assert upstream_url("http://meeting-api", SID) == f"ws://meeting-api{VNC}"


# ── the REST rows ───────────────────────────────────────────────────────────────────────────────

def test_the_view_route_returns_the_bytes_and_content_type_meeting_api_answered():
    downstream = FakeDownstream(content_type="text/html; charset=utf-8")
    client, _ = _client(downstream=downstream)
    r = client.get(f"/bot-accounts/sessions/{SID}/view/app/ui.js", headers={"x-api-key": VALID_KEY})
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert downstream.last["url"] == f"http://meeting-api/bot-accounts/sessions/{SID}/view/app/ui.js"


@pytest.mark.parametrize("path", ["../../bots", "%2E%2E/x", "a%2Fb"])
def test_the_view_route_refuses_a_tail_that_would_leave_the_session(path):
    downstream = FakeDownstream()
    client, _ = _client(downstream=downstream)
    r = client.get(f"/bot-accounts/sessions/{SID}/view/{path}", headers={"x-api-key": VALID_KEY})
    assert r.status_code in (400, 404, 422)  # 422: the client resolved `..` onto another row
    assert downstream.last is None


def test_an_id_that_is_not_32_hex_is_refused_before_any_forward():
    downstream = FakeDownstream()
    client, _ = _client(downstream=downstream)
    for method, path in (("GET", "/bot-accounts/sessions/xyz"), ("DELETE", "/bot-accounts/xyz"),
                         ("POST", "/bot-accounts/xyz/verify")):
        assert client.request(method, path, headers={"x-api-key": VALID_KEY}).status_code == 422
    assert downstream.last is None
