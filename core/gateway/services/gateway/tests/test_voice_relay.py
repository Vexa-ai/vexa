"""The voice-note socket at the edge (#1783): authorized once at the upgrade against the meetings
row, then relayed to meeting-api verbatim with the signed identity, close codes carried both ways."""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway import create_app
from gateway.voice_relay import UpstreamClosed

from conftest import VALID_KEY, VALID_USER, FakeAuthorizer, FakeDownstream, FakeRedis


class FakeUpstream:
    """meeting-api's socket: says ready, records what arrives, answers stop and closes 1000."""

    def __init__(self) -> None:
        self.received: list = []
        self.closed_with = None
        self._out: asyncio.Queue = asyncio.Queue()
        self._out.put_nowait(json.dumps({"type": "ready"}))

    async def send(self, data) -> None:
        self.received.append(data)
        if isinstance(data, str) and json.loads(data).get("type") == "stop":
            await self._out.put(json.dumps({"type": "stopped"}))
            await self._out.put(UpstreamClosed(1000))

    async def recv(self):
        item = await self._out.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


class FakeDialer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list = []
        self.upstream = FakeUpstream()

    def connect(self, url, headers):
        self.calls.append((url, dict(headers)))
        dialer = self

        @contextlib.asynccontextmanager
        async def cm():
            if dialer.fail:
                raise ConnectionRefusedError("meeting-api down")
            yield dialer.upstream

        return cm()


def _client(dialer, user=None):
    app = create_app(FakeAuthorizer(user=user), FakeDownstream(), FakeRedis(),
                     meeting_api_url="http://meeting-api:8080", voice_note_dialer=dialer)
    return TestClient(app)


PATH = "/voice-notes/vn-abc/stream"


def test_frames_are_relayed_both_ways_with_the_signed_identity():
    dialer = FakeDialer()
    with _client(dialer).websocket_connect(PATH, headers={"x-api-key": VALID_KEY}) as ws:
        assert ws.receive_json() == {"type": "ready"}
        ws.send_bytes(b"\x00" * 16)
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json() == {"type": "stopped"}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 1000
    url, headers = dialer.calls[0]
    assert url == "ws://meeting-api:8080/voice-notes/vn-abc/stream"
    assert headers["x-user-id"] == str(VALID_USER["user_id"])
    assert "x-api-key" not in {k.lower() for k in headers}
    assert dialer.upstream.received[0] == b"\x00" * 16


@pytest.mark.parametrize("headers,error,code", [
    ({}, "missing_api_key", 4401),
    ({"x-api-key": "nope"}, "invalid_api_key", 4401),
])
def test_a_caller_the_edge_does_not_accept_is_refused_before_any_dial(headers, error, code):
    dialer = FakeDialer()
    with _client(dialer).websocket_connect(PATH, headers=headers) as ws:
        assert ws.receive_json() == {"type": "error", "error": error}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == code
    assert dialer.calls == []


def test_a_browser_only_key_is_refused():
    dialer = FakeDialer()
    user = {**VALID_USER, "scopes": ["browser"]}
    with _client(dialer, user=user).websocket_connect(PATH, headers={"x-api-key": VALID_KEY}) as ws:
        assert ws.receive_json() == {"type": "error", "error": "insufficient_scope"}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4403
    assert dialer.calls == []


def test_an_unreachable_meeting_api_is_a_typed_close():
    with _client(FakeDialer(fail=True)).websocket_connect(PATH, headers={"x-api-key": VALID_KEY}) as ws:
        assert ws.receive_json() == {"type": "error", "error": "upstream_unavailable"}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4502


def test_a_plain_get_is_forwarded_like_any_meetings_row():
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(), downstream, FakeRedis(), voice_note_dialer=FakeDialer())
    TestClient(app).get(PATH, headers={"x-api-key": VALID_KEY})
    assert downstream.last["url"].endswith("/voice-notes/vn-abc/stream")


def test_the_per_user_rate_limit_applies_when_the_socket_opens():
    from gateway.ratelimit import PerUserRateLimiter

    dialer = FakeDialer()
    limiter = PerUserRateLimiter(capacity=1, refill_per_sec=0)
    app = create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis(),
                     meeting_api_url="http://meeting-api:8080", voice_note_dialer=dialer,
                     rate_limiter=limiter)
    client = TestClient(app)
    with client.websocket_connect(PATH, headers={"x-api-key": VALID_KEY}) as ws:
        assert ws.receive_json() == {"type": "ready"}
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert len(dialer.calls) == 1
    dialer.upstream = FakeUpstream()
    with client.websocket_connect(PATH, headers={"x-api-key": VALID_KEY}) as ws:
        assert ws.receive_json() == {"type": "error", "error": "rate_limited"}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4429
    assert len(dialer.calls) == 1
