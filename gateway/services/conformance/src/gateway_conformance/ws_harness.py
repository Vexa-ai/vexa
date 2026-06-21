"""The `/ws` multiplex conformance harness — fakes that satisfy the PRODUCTION gateway's
ports, driving `gateway.app._run_multiplex` (the SHIPPED control loop + fan-in).

Before the carve `WSMultiplexHarness.run` re-implemented `main.websocket_multiplex` by hand.
It is now a **thin driver**: `run()` calls the production `_run_multiplex` against these fakes,
so every ws.v1 assertion in `test_ws_protocol.py` exercises the shipped multiplex.

The fakes:
- `FakeWebSocket` — captures every `send_text` (so a test asserts the JSON frame the gateway
  emitted: subscribed / unsubscribed / pong / error) and serves a scripted queue of client
  messages. `receive_text` raises starlette's `WebSocketDisconnect` when the client hangs up —
  exactly the exception the production loop catches.
- `FakeRedis` / `FakePubSub` — an in-process publish→subscribe fan-in (`pubsub()` →
  subscribe/unsubscribe/listen). A payload PUBLISHed to a subscribed channel is forwarded
  VERBATIM to the socket, as the production `fan_in` does (forwards the raw redis payload).
- `CollectorAuthorizer` — the gateway's `Authorizer` port, whose `authorize_subscribe` hop now
  POSTs the REAL, SHIPPED collector's `/ws/authorize-subscribe` (`transcription_collector.create_app`)
  over an in-process ASGI transport, instead of a hand-rolled fake. The collector's production code
  decides authorization (DB-ownership boundary) and returns the `authorized` list (with `meeting_id`
  so the gateway knows which redis channels to fan in). `resolve` stays a stub (the `/ws` loop only
  needs a non-empty api_key). `FakeAuthorizer` is kept as a thin alias for backward-compatible
  construction from an `(platform, native_id) → {meeting_id, user_id}` map — it seeds the real
  collector's store from that map, so the same tests now drive shipped collector code.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx
from starlette.websockets import WebSocketDisconnect

from transcription_collector import create_app as create_collector_app
from transcription_collector.fakes import InMemoryTranscriptStore

from gateway.app import _run_multiplex


class FakeWebSocket:
    """Records sent frames; serves scripted client messages."""

    def __init__(self, inbound: Optional[list[str]] = None, api_key: Optional[str] = None,
                 close_when_drained: bool = True):
        self._inbound = asyncio.Queue()
        # close_when_drained=True (default): once the scripted messages are consumed the loop
        # ends (like a client that sent its frames and hung up) — used by the one-shot error
        # tests. =False: receive_text() blocks after draining so the subscription stays live for
        # fan-in (used by the forwarded-frame tests, which call disconnect() explicitly).
        self._close_when_drained = close_when_drained
        for m in inbound or []:
            self._inbound.put_nowait(m)
        self.sent: list[dict[str, Any]] = []   # decoded JSON frames the gateway emitted
        self.sent_raw: list[str] = []           # raw strings (data frames are forwarded raw)
        # Fires on every send_text so a test can await a forwarded frame DETERMINISTICALLY
        # (wait_for_sent) instead of guessing how many sleep(0) turns the fan_in needs.
        self._sent_event = asyncio.Event()
        self.close_code: Optional[int] = None
        self.headers = {"x-api-key": api_key} if api_key else {}
        self.query_params: dict[str, str] = {}
        # Set by the test to end the (otherwise-blocking) receive loop, mirroring a real client
        # disconnect. While clear, receive_text() blocks so the subscription stays live and the
        # production fan_in can forward redis payloads — exactly like the shipped loop.
        self._client_gone = asyncio.Event()

    async def accept(self) -> None:
        pass

    async def send_text(self, data: str) -> None:
        self.sent_raw.append(data)
        try:
            self.sent.append(json.loads(data))
        except Exception:
            self.sent.append({"__raw__": data})
        self._sent_event.set()

    async def wait_for_sent(self, count: int, timeout: float = 5.0) -> None:
        """Block until at least `count` frames have been emitted. Event-driven, so the
        caller resumes the instant the gateway forwards — no sleep(0) turn-counting. The
        timeout is a hang guard only: on expiry it returns and the test's own assertion
        (e.g. ``no segment frame forwarded; frames=…``) reports the missing frame."""
        while len(self.sent) < count:
            self._sent_event.clear()
            if len(self.sent) >= count:   # recheck after clear — no await between, no lost wakeup
                return
            try:
                await asyncio.wait_for(self._sent_event.wait(), timeout)
            except (asyncio.TimeoutError, TimeoutError):
                return

    def disconnect(self) -> None:
        """Signal the client has gone — the blocking receive_text() then raises WebSocketDisconnect."""
        self._client_gone.set()

    async def receive_text(self) -> str:
        if not self._inbound.empty():
            return await self._inbound.get()
        if self._close_when_drained:
            raise WebSocketDisconnect(code=1000)  # scripted messages done → loop ends like a hangup
        # Inbound drained: block (like a real socket awaiting the next client frame) until the
        # test disconnects. This keeps subscriptions alive so fan_in can forward.
        getter = asyncio.ensure_future(self._inbound.get())
        closer = asyncio.ensure_future(self._client_gone.wait())
        done, pending = await asyncio.wait({getter, closer}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if getter in done and not getter.cancelled():
            return getter.result()
        raise WebSocketDisconnect(code=1000)  # client gone → loop ends like a real disconnect

    async def close(self, code: int = 1000) -> None:
        self.close_code = code


class FakePubSub:
    def __init__(self, hub: "FakeRedis"):
        self._hub = hub
        self._queue: asyncio.Queue = asyncio.Queue()
        self._channels: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        self._channels = list(channels)
        for ch in channels:
            self._hub._subs.setdefault(ch, []).append(self._queue)
            self._hub._notify_subscribed(ch)   # wake any deliver() awaiting this channel

    async def unsubscribe(self, *channels: str) -> None:
        for ch in channels or self._channels:
            try:
                self._hub._subs.get(ch, []).remove(self._queue)
            except ValueError:
                pass

    async def close(self) -> None:
        pass

    async def listen(self):
        # Yield a subscribe confirmation then stream messages (mirrors redis-py pubsub.listen).
        yield {"type": "subscribe"}
        while True:
            data = await self._queue.get()
            yield {"type": "message", "data": data}


class FakeRedis:
    """Satisfies `gateway.ports.RedisBus`: in-process pub/sub hub. `publish` delivers to every
    subscriber's queue."""

    def __init__(self):
        self._subs: dict[str, list[asyncio.Queue]] = {}
        # Per-channel "a fan_in task has registered a subscription" signal. The production
        # subscribe path is `asyncio.create_task(fan_in())` → `await pubsub.subscribe()`, so
        # the subscription registers some unknown number of loop turns after subscribe is
        # requested. wait_for_subscriber() awaits this event instead of guessing with
        # sleep(0) — the race that dropped delivered payloads before the subscriber existed.
        self._sub_events: dict[str, asyncio.Event] = {}

    def _sub_event(self, channel: str) -> asyncio.Event:
        return self._sub_events.setdefault(channel, asyncio.Event())

    def _notify_subscribed(self, channel: str) -> None:
        self._sub_event(channel).set()

    def has_subscriber(self, channel: str) -> bool:
        return bool(self._subs.get(channel))

    async def wait_for_subscriber(self, channel: str, timeout: float = 5.0) -> None:
        """Block until a fan_in task has registered a subscription on `channel`. The
        readiness handshake that makes delivery deterministic: it returns only once a
        queue is registered, so a subsequent publish() can never be dropped for lack of a
        subscriber. The timeout is a hang guard; on expiry it returns and the caller's
        publish is a no-op the test then reports."""
        while not self.has_subscriber(channel):
            self._sub_event(channel).clear()
            if self.has_subscriber(channel):   # recheck after clear — no await between, no lost wakeup
                return
            try:
                await asyncio.wait_for(self._sub_event(channel).wait(), timeout)
            except (asyncio.TimeoutError, TimeoutError):
                return

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)

    async def publish(self, channel: str, data: str) -> None:
        for q in list(self._subs.get(channel, [])):
            await q.put(data)


class CollectorAuthorizer:
    """Satisfies `gateway.ports.Authorizer` for the `/ws` path by POSTing the REAL, SHIPPED
    collector's `/ws/authorize-subscribe` (`transcription_collector.create_app`) over an in-process
    ASGI transport — exactly the gateway's `AdminApiAuthorizer.authorize_subscribe` hop, but offline.

    Constructed from an `(platform, native_meeting_id) → {meeting_id, user_id}` map: it seeds the
    real collector's in-memory store from that map (so the collector's PRODUCTION authorization code
    — the DB-ownership boundary — decides which meetings are authorized) and forwards the resolved
    user's identity in the `x-user-id` header the gateway injects."""

    def __init__(self, mapping: dict[tuple[str, str], dict[str, Any]]):
        # (platform, native_meeting_id) → {"meeting_id": int, "user_id": int}
        self._mapping = mapping
        # The single resolved user for this socket (all AUTH_MAP entries share VALID_USER's id).
        self._user_id = next(iter(mapping.values()), {}).get("user_id", 7) if mapping else 7
        store = InMemoryTranscriptStore()
        for (platform, native_id), info in mapping.items():
            store.seed_meeting(
                user_id=info["user_id"], platform=platform, native_meeting_id=native_id,
                meeting_id=info["meeting_id"], status="active",
            )
        # The SHIPPED collector app, reached over an in-process transport (no sockets).
        app = create_collector_app(store, redis=None)
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://transcription-collector"
        )

    async def resolve(self, api_key: str) -> Optional[dict]:
        # The /ws loop only needs a non-empty api_key (it doesn't call resolve()); provided for
        # port-completeness.
        return {"user_id": self._user_id, "scopes": ["bot", "tx", "browser"], "max_concurrent": 3}

    async def authorize_subscribe(self, api_key: str, meetings: list[dict[str, str]]) -> dict[str, Any]:
        resp = await self._client.post(
            "/ws/authorize-subscribe",
            headers={"x-user-id": str(self._user_id), "x-api-key": api_key},
            json={"meetings": meetings},
        )
        if resp.status_code != 200:
            return {"authorized": [], "errors": [f"authorization_service_error:{resp.status_code}"]}
        return resp.json()


# Backward-compatible alias: the existing /ws-protocol tests construct `FakeAuthorizer(AUTH_MAP)`.
# That name now resolves to the CollectorAuthorizer, so those tests drive the SHIPPED collector's
# `/ws/authorize-subscribe` instead of a hand-rolled fake.
FakeAuthorizer = CollectorAuthorizer


class WSMultiplexHarness:
    """Drives the PRODUCTION `gateway.app._run_multiplex` against the fakes above."""

    def __init__(self, ws: FakeWebSocket, redis: FakeRedis, authorizer: FakeAuthorizer):
        self.ws = ws
        self.redis = redis
        self.authorizer = authorizer

    async def run(self) -> None:
        """Drive the shipped control loop until the client message queue drains / disconnects."""
        await _run_multiplex(self.ws, self.authorizer, self.redis)

    async def deliver(self, channel: str, payload: dict[str, Any],
                      *, expect_forward: bool = True) -> None:
        """Publish a redis payload and (by default) block until the production fan_in has
        actually forwarded it to the socket — a DETERMINISTIC handshake, not sleep(0)
        guesswork:

          wait_for_subscriber(channel)  → the fan_in task has registered its subscription,
          publish(channel, payload)     → deliver to that subscriber's queue,
          ws.wait_for_sent(before + 1)  → the fan_in dequeued and forwarded the frame.

        With ``expect_forward=False`` (a channel with no live subscription — e.g. after an
        unsubscribe) it only publishes: there is no subscriber to register and no frame to
        forward, so the caller asserts the payload was dropped."""
        if expect_forward:
            await self.redis.wait_for_subscriber(channel)
        before = len(self.ws.sent)
        await self.redis.publish(channel, json.dumps(payload))
        if expect_forward:
            await self.ws.wait_for_sent(before + 1)
