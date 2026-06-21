"""Focused unit tests for the ``/ws`` multiplex half of the gateway (``_run_multiplex``).

Drives the control loop directly with a fake WebSocket (no real socket, no real redis),
proving the carve of ``main.websocket_multiplex``:
  * missing api-key → missing_api_key error + close 4401,
  * subscribe → subscribed ack; a redis payload on a subscribed channel is forwarded RAW,
  * unsubscribe → unsubscribed ack AND the fan-in STOPS (later payloads are not forwarded),
  * ping → pong; invalid_json / unknown_action error frames.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from gateway.app import _run_multiplex
from conftest import FakeAuthorizer, FakeRedis

API_KEY = "vxa_test_unit_key"
AUTH_MAP = {("google_meet", "room-1"): {"meeting_id": 42, "user_id": 7}}
SUBSCRIBE = {"action": "subscribe", "meetings": [{"platform": "google_meet", "native_id": "room-1"}]}


class WSClosed(Exception):
    def __init__(self, code: int = 1000):
        self.code = code


class FakeWebSocket:
    def __init__(self, inbound=None, api_key: Optional[str] = None, close_when_drained=True):
        self._inbound = asyncio.Queue()
        self._close_when_drained = close_when_drained
        for m in inbound or []:
            self._inbound.put_nowait(json.dumps(m) if isinstance(m, dict) else m)
        self.sent: list[dict] = []
        self.close_code: Optional[int] = None
        self.headers = {"x-api-key": api_key} if api_key else {}
        self.query_params: dict[str, str] = {}
        self._gone = asyncio.Event()

    async def accept(self) -> None:
        pass

    async def send_text(self, data: str) -> None:
        try:
            self.sent.append(json.loads(data))
        except Exception:
            self.sent.append({"__raw__": data})

    def disconnect(self) -> None:
        self._gone.set()

    async def receive_text(self) -> str:
        if not self._inbound.empty():
            return await self._inbound.get()
        if self._close_when_drained:
            raise WSClosed()
        getter = asyncio.ensure_future(self._inbound.get())
        closer = asyncio.ensure_future(self._gone.wait())
        done, pending = await asyncio.wait({getter, closer}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if getter in done and not getter.cancelled():
            return getter.result()
        raise WSClosed()

    async def close(self, code: int = 1000) -> None:
        self.close_code = code


# _run_multiplex catches WebSocketDisconnect from starlette; our FakeWebSocket raises WSClosed,
# which is NOT that type — so the loop's receive_text() must translate. We patch by feeding a
# disconnect through the queue draining + close_when_drained semantics instead.
import gateway.app as gw_app
from starlette.websockets import WebSocketDisconnect


class _WS(FakeWebSocket):
    async def receive_text(self) -> str:
        try:
            return await super().receive_text()
        except WSClosed:
            raise WebSocketDisconnect(code=1000)


def _redis_and_auth():
    return FakeRedis(), FakeAuthorizer(valid_key=API_KEY, auth_map=AUTH_MAP)


async def test_missing_api_key_closes_4401():
    ws = _WS(inbound=[], api_key=None)
    redis, auth = _redis_and_auth()
    await _run_multiplex(ws, auth, redis)
    assert ws.sent and ws.sent[0]["error"] == "missing_api_key"
    assert ws.close_code == 4401


async def test_ping_pong():
    ws = _WS(inbound=[{"action": "ping"}], api_key=API_KEY)
    redis, auth = _redis_and_auth()
    await _run_multiplex(ws, auth, redis)
    assert {"type": "pong"} in ws.sent


async def test_unknown_action_and_invalid_json():
    ws = _WS(inbound=["not-json{", {"action": "frobnicate"}], api_key=API_KEY)
    redis, auth = _redis_and_auth()
    await _run_multiplex(ws, auth, redis)
    errs = [f.get("error") for f in ws.sent if f.get("type") == "error"]
    assert "invalid_json" in errs and "unknown_action" in errs


async def test_subscribe_acks_and_forwards_then_unsubscribe_stops():
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY, close_when_drained=False)
    redis, auth = _redis_and_auth()
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    for _ in range(10):
        await asyncio.sleep(0)
    assert any(f.get("type") == "subscribed" for f in ws.sent), ws.sent

    # a payload on a subscribed channel is forwarded RAW
    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "hi"}))
    for _ in range(10):
        await asyncio.sleep(0)
    assert any(f.get("type") == "transcription_segment" for f in ws.sent), ws.sent

    # unsubscribe → ack + fan-in stops
    ws._inbound.put_nowait(json.dumps({
        "action": "unsubscribe", "meetings": [{"platform": "google_meet", "native_id": "room-1"}]}))
    for _ in range(10):
        await asyncio.sleep(0)
    assert any(f.get("type") == "unsubscribed" for f in ws.sent), ws.sent

    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "after"}))
    for _ in range(10):
        await asyncio.sleep(0)
    assert not any(f.get("type") == "transcription_segment" and f.get("text") == "after" for f in ws.sent), \
        "fan-in must STOP after unsubscribe"

    ws.disconnect()
    await task
