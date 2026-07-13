"""Injected in-process fakes for the gateway package's own unit tests.

The gateway package must NOT import the conformance harness (import direction is one-way:
conformance → gateway). So these focused fakes live HERE, satisfying the ``ports.py``
Protocols structurally, to prove ``create_app`` in isolation: a fake admin-api authorizer, a
fake downstream that echoes a recorded reply, and a tiny in-process redis pub/sub.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

# fastapi-guard: keep it installed (so the integration is exercised) but in-memory and with
# rate limiting off, so the unit suite never depends on Redis and guard can never throttle/
# block a unit-test request. GUARD_WS_ENABLED is left unset (default false) so the WS path
# is unchanged for the existing multiplex suite.
os.environ.setdefault("GUARD_ENABLED", "true")
os.environ.setdefault("GUARD_ENABLE_REDIS", "false")
os.environ.setdefault("GUARD_RATE_LIMIT_RPM", "0")

VALID_KEY = "vxa_test_unit_key"
VALID_USER = {"user_id": 7, "scopes": ["bot", "tx", "browser"], "max_concurrent": 3, "email": "u@example.com"}


class FakeAuthorizer:
    """Satisfies ``ports.Authorizer``: resolve a single valid key, authorize a fixed subscribe."""

    def __init__(self, user: Optional[dict] = None, valid_key: str = VALID_KEY,
                 auth_map: Optional[dict] = None):
        self._user = dict(user or VALID_USER)
        self._valid_key = valid_key
        # (platform, native_meeting_id) → {"meeting_id", "user_id"}
        self._auth_map = auth_map or {}

    async def resolve(self, api_key: str) -> Optional[dict]:
        if api_key == self._valid_key:
            return dict(self._user)
        return None

    async def authorize_subscribe(self, api_key: str, meetings: list) -> dict:
        authorized, errors = [], []
        for m in meetings:
            key = (m.get("platform"), m.get("native_meeting_id"))
            info = self._auth_map.get(key)
            if info:
                authorized.append({"platform": key[0], "native_id": key[1],
                                   "user_id": info["user_id"], "meeting_id": info["meeting_id"]})
            else:
                errors.append(f"{key} not authorized")
        return {"authorized": authorized, "errors": errors}


class _Resp:
    def __init__(self, status_code: int, content: bytes, headers: dict):
        self.status_code = status_code
        self.content = content
        self.headers = headers


class FakeDownstream:
    """Satisfies ``ports.DownstreamClient``: records the last forward and returns a canned reply."""

    def __init__(self, status_code: int = 200, body: Optional[dict] = None,
                 content_type: str = "application/json",
                 stream_chunks: Optional[list] = None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True}
        self._content_type = content_type
        # canned SSE frames for the streaming (agent chat) path
        self._stream_chunks = stream_chunks if stream_chunks is not None else [
            b'data: {"type":"token","text":"hi"}\n\n',
            b'data: {"type":"done"}\n\n',
        ]
        self.last: Optional[dict] = None

    async def request(self, method, url, *, headers=None, params=None, content=None):
        self.last = {"method": method, "url": url, "headers": headers or {},
                     "params": params, "content": content}
        return _Resp(self.status_code, json.dumps(self._body).encode(),
                     {"content-type": self._content_type})

    async def stream(self, method, url, *, headers=None, params=None, content=None):
        self.last = {"method": method, "url": url, "headers": headers or {},
                     "params": params, "content": content}
        for chunk in self._stream_chunks:
            yield chunk


class FakePubSub:
    def __init__(self, hub: "FakeRedis"):
        self._hub = hub
        self._queue: asyncio.Queue = asyncio.Queue()
        self._channels: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        self._channels = list(channels)
        for ch in channels:
            self._hub._subs.setdefault(ch, []).append(self._queue)

    async def unsubscribe(self, *channels: str) -> None:
        for ch in channels or self._channels:
            try:
                self._hub._subs.get(ch, []).remove(self._queue)
            except ValueError:
                pass

    async def close(self) -> None:
        pass

    async def listen(self):
        yield {"type": "subscribe"}
        while True:
            data = await self._queue.get()
            yield {"type": "message", "data": data}


class FakeRedis:
    """Satisfies ``ports.RedisBus``: in-process pub/sub hub for the /ws fan-in."""

    def __init__(self):
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)

    async def publish(self, channel: str, data: str) -> None:
        for q in list(self._subs.get(channel, [])):
            await q.put(data)
