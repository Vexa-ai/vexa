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

import pytest
import json
from typing import Optional

from gateway.multiplex import _run_multiplex
from gateway.ports import AuthUnavailable
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
import gateway.multiplex as gw_app
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


class _UnavailableAuthorizer:
    """resolve() raises AuthUnavailable — the auth-infra-down shape on the WS connect hop (#495)."""
    async def resolve(self, api_key):
        raise AuthUnavailable("admin-api validate unreachable (test)")
    async def authorize_subscribe(self, api_key, meetings):
        return {"authorized": [], "errors": ["unavailable"]}


async def test_auth_infra_unavailable_closes_4503_not_4401():
    """#495 regression guard: when the validation hop is DOWN at WS connect, a VALID key must not
    crash the socket (uncaught raise → 1006/1011) NOR be told it's invalid (4401). It gets a typed
    auth_unavailable frame + a distinct retryable close code (4503)."""
    ws = _WS(inbound=[], api_key=API_KEY)
    redis = FakeRedis()
    await _run_multiplex(ws, _UnavailableAuthorizer(), redis)
    assert ws.sent and ws.sent[0]["error"] == "auth_unavailable"
    assert ws.close_code == 4503
    assert ws.sent[0]["error"] != "invalid_api_key"


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


async def test_invalid_api_key_closes_4401():
    # Track G (meeting-status-ws §C.2): connect now RESOLVES the key (not just presence). A
    # present-but-invalid key fails closed like the proxy — invalid_api_key + close 4401.
    ws = _WS(inbound=[], api_key="vxa_not_a_real_key")
    redis, auth = _redis_and_auth()
    await _run_multiplex(ws, auth, redis)
    assert ws.sent and ws.sent[0]["error"] == "invalid_api_key"
    assert ws.close_code == 4401


async def test_connect_auto_subscribes_user_channel_and_forwards():
    # Track G: a valid connect resolves user_id (7) and auto-subscribes `u:7:meetings`, with no
    # client `subscribe` frame. A frame published to that channel reaches the socket verbatim.
    ws = _WS(inbound=[], api_key=API_KEY, close_when_drained=False)
    redis, auth = _redis_and_auth()
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    for _ in range(10):
        await asyncio.sleep(0)

    # user_id from AUTH/FakeAuthorizer is 7 → channel u:7:meetings. No subscribe frame was sent.
    frame = {
        "type": "meeting.status",
        "meeting": {"id": 42, "platform": "google_meet", "native_id": "room-1"},
        "payload": {"status": "scheduled"},
        "user_id": 7,
        "meeting_id": 42,
        "native": "room-1",
        "status": "scheduled",
    }
    await redis.publish("u:7:meetings", json.dumps(frame))
    for _ in range(10):
        await asyncio.sleep(0)
    assert any(
        f.get("type") == "meeting.status" and f.get("status") == "scheduled" for f in ws.sent
    ), ws.sent

    ws.disconnect()
    await task


async def test_connect_also_fans_in_the_persons_own_notifications():
    """#1783: the socket hears `u:{user_id}:notifications` — the person's own notifications and rail
    frames — with no subscribe frame, and never another person's channel."""
    ws = _WS(inbound=[], api_key=API_KEY, close_when_drained=False)
    redis, auth = _redis_and_auth()
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    for _ in range(10):
        await asyncio.sleep(0)
    mine = {"type": "notification", "notification": {"id": "n-1", "kind": "chat_added", "text": "x"}}
    theirs = {"type": "notification", "notification": {"id": "n-2", "kind": "chat_added", "text": "y"}}
    await redis.publish("u:7:notifications", json.dumps(mine))
    await redis.publish("u:8:notifications", json.dumps(theirs))
    await redis.publish("u:7:notifications", json.dumps({"type": "rail", "change": "added", "session": "s-1"}))
    for _ in range(10):
        await asyncio.sleep(0)
    got = [f for f in ws.sent if f.get("type") in ("notification", "rail")]
    assert [f.get("notification", {}).get("id") or f["type"] for f in got] == ["n-1", "rail"], ws.sent
    ws.disconnect()
    await task


# ── /ws takes the REST rows' scopes, all ["bot","tx"] (#1922, one_key_scope) ─────────────────────
#
# Any key holding bot or tx opens the socket and subscribes; WHICH meetings it gets is the authorize
# hop's answer (ownership, workspace, share), as for any key. No bot/tx split is computed or refused;
# only a browser-only key, which holds neither, is.

class _ScopedAuthorizer(FakeAuthorizer):
    """A key with the given scopes; records whether the subscribe hop was asked at all."""

    def __init__(self, scopes):
        super().__init__(user={"user_id": 7, "scopes": list(scopes), "max_concurrent": 3},
                         valid_key=API_KEY, auth_map=AUTH_MAP)
        self.asked = 0

    async def authorize_subscribe(self, api_key, meetings):
        self.asked += 1
        return await super().authorize_subscribe(api_key, meetings)


def _capture(monkeypatch):
    import gateway.changed as changed

    events = []
    monkeypatch.setattr(gw_app, "log_event", lambda name, **kw: events.append((name, kw)))
    monkeypatch.setattr(changed, "COMPAT_USE", {})
    return events


def _not_enforced(events):
    """Any scope-split event: there must be none."""
    return [(name, kw) for name, kw in events if name in ("ws_scope_not_enforced", "request_denied_scope")]


@pytest.mark.parametrize("old_setting", [None, "legacy"])
async def test_a_browser_only_key_opens_no_socket(monkeypatch, old_setting):
    """A key that may read no meeting status over REST (`GET /meetings` takes `tx`, `GET /bots/status`
    takes `bot`) opens no socket (every row is ["bot","tx"] now): `insufficient_scope`, then close 4403, whatever the removed
    `VEXA_WS_SCOPE_MODE` says."""
    if old_setting:
        monkeypatch.setenv("VEXA_WS_SCOPE_MODE", old_setting)
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY)
    redis, auth = FakeRedis(), _ScopedAuthorizer(["browser"])
    await _run_multiplex(ws, auth, redis)
    assert ws.sent and ws.sent[0]["error"] == "insufficient_scope"
    assert ws.sent[0]["changed_in"] == "0.13.4" and "scope" in ws.sent[0]["do_instead"]
    assert "bot or tx" in ws.sent[0]["do_instead"]
    assert ws.sent[0]["docs"] == "https://docs.vexa.ai/changes-0-13-4-auth"
    assert ws.close_code == 4403
    assert auth.asked == 0


async def _settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


@pytest.mark.parametrize("old_setting", [None, "legacy", "strict"])
async def test_a_bot_only_key_subscribes_to_its_own_meeting_and_gets_its_frames(monkeypatch, old_setting):
    """`{bot}` is the hosted webapp's key: it streams its own meeting's transcript as in 0.12.28. The
    removed `VEXA_WS_SCOPE_MODE` changes nothing either way."""
    if old_setting:
        monkeypatch.setenv("VEXA_WS_SCOPE_MODE", old_setting)
    events = _capture(monkeypatch)
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY, close_when_drained=False)
    redis, auth = FakeRedis(), _ScopedAuthorizer(["bot"])
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    assert {"type": "subscribed", "meetings": [{"platform": "google_meet", "native_id": "room-1"}]} in ws.sent
    assert not [f for f in ws.sent if f.get("type") == "error"], ws.sent
    assert auth.asked == 1                                 # ownership is still the hop's answer
    await redis.publish("tc:meeting:42:mutable", '{"type": "transcript", "speaker": "A"}')
    await _settle()
    assert {"type": "transcript", "speaker": "A"} in ws.sent
    assert not _not_enforced(events), events
    ws.disconnect()
    await asyncio.wait_for(task, timeout=2)


async def test_a_bot_only_key_gets_nothing_for_a_meeting_it_may_not_read(monkeypatch):
    _capture(monkeypatch)
    other = {"action": "subscribe", "meetings": [{"platform": "google_meet", "native_id": "room-9"}]}
    ws = _WS(inbound=[other], api_key=API_KEY, close_when_drained=False)
    redis, auth = FakeRedis(), _ScopedAuthorizer(["bot"])
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    assert not any(f.get("type") == "subscribed" and f.get("meetings") for f in ws.sent), ws.sent
    await redis.publish("tc:meeting:42:mutable", '{"type": "transcript", "speaker": "A"}')
    await _settle()
    assert {"type": "transcript", "speaker": "A"} not in ws.sent
    ws.disconnect()
    await asyncio.wait_for(task, timeout=2)


async def test_a_transcript_key_still_subscribes_and_is_not_counted(monkeypatch):
    events = _capture(monkeypatch)
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY, close_when_drained=False)
    redis, auth = FakeRedis(), _ScopedAuthorizer(["tx"])
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    assert any(f.get("type") == "subscribed" and f["meetings"] for f in ws.sent), ws.sent
    assert not _not_enforced(events)
    ws.disconnect()
    await asyncio.wait_for(task, timeout=2)


async def test_a_key_in_the_url_connects_and_subscribes(monkeypatch):
    events = _capture(monkeypatch)
    ws = _WS(inbound=[SUBSCRIBE], api_key=None, close_when_drained=False)
    ws.query_params = {"api_key": API_KEY}
    redis, auth = FakeRedis(), _ScopedAuthorizer(["bot", "tx"])
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    assert any(f.get("type") == "subscribed" and f["meetings"] for f in ws.sent), ws.sent
    assert [kw["fields"]["count"] for name, kw in events if name == "ws_query_key_deprecated"] == [1]
    ws.disconnect()
    await asyncio.wait_for(task, timeout=2)


async def test_the_socket_reads_its_scopes_from_the_route_table(monkeypatch):
    """The socket holds no table of its own: it takes the REST rows' scopes, so a table in which the
    transcript read took only `tx` would refuse a `bot` key's subscription (the assembled table is
    ["bot","tx"] everywhere, #1922, so in practice only a browser-only key is refused)."""
    _capture(monkeypatch)
    table = {("GET", "/transcripts/{platform}/{native_meeting_id}"): frozenset({"tx"}),
             ("GET", "/meetings"): frozenset({"bot", "tx"}), ("GET", "/bots/status"): frozenset({"bot", "tx"})}
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY, close_when_drained=False)
    redis, auth = FakeRedis(), _ScopedAuthorizer(["bot"])
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis, route_scopes=table))
    for _ in range(10):
        await asyncio.sleep(0)
    assert any(f.get("error") == "insufficient_scope" for f in ws.sent), ws.sent
    assert not any(f.get("type") == "subscribed" and f["meetings"] for f in ws.sent), ws.sent
    assert auth.asked == 0
    ws.disconnect()
    await asyncio.wait_for(task, timeout=2)


