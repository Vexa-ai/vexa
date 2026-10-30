"""acts.v1 ingress adapter — redis pub/sub subscriber.

Mirrors ``services/bot/src/adapters/acts-redis.ts``: subscribes to the meeting's command bus
``bot_commands:meeting:{meeting_id}``, JSON-parses each message, validates it against acts.v1
(``discord_bot.contracts.parse_act`` — off-contract/unrecognized messages are IGNORED, never
raised, per the acts.v1 README), and calls ``handler(act)`` for every recognized command.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional, Protocol

from discord_bot.contracts import parse_act


def acts_channel(meeting_id: Any) -> str:
    return f"bot_commands:meeting:{meeting_id}"


class RedisActsClient(Protocol):
    async def subscribe(self, channel: str, handler: Callable[[str], Any]) -> Any: ...


class RedisPubSubClient:
    """Adapts a real ``redis.asyncio.Redis`` client's pubsub API (a separate object with a
    ``listen()`` iterator) to the ``RedisActsClient`` port's plain ``subscribe(channel, handler)``
    callback shape. Shared by the real composition root (``bot.py``) and the mock one
    (``mock/main.py``) — the one place that knows how redis.asyncio's pubsub actually works."""

    def __init__(self, client: Any):
        self._client = client
        self._pubsub: Optional[Any] = None
        self._task: Optional[asyncio.Task] = None

    async def subscribe(self, channel: str, handler: Callable[[str], Awaitable[None]]) -> None:
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(channel)

        async def _listen() -> None:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                await handler(data)

        self._task = asyncio.create_task(_listen())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._pubsub:
            await self._pubsub.close()


class ActsSource:
    def __init__(self, client: RedisActsClient, meeting_id: Any, log: Optional[Callable[[str], None]] = None):
        self._client = client
        self._channel = acts_channel(meeting_id)
        self._log = log or (lambda m: print(m, flush=True))

    async def subscribe(self, handler: Callable[[dict], Awaitable[None]]) -> None:
        async def on_message(message: str) -> None:
            try:
                decoded = json.loads(message)
            except (TypeError, ValueError):
                self._log(f"[discord-bot] acts.v1: dropped non-JSON message on {self._channel}")
                return
            act = parse_act(decoded)
            if act is None:
                return  # off-contract or unrecognized action → ignored (acts.v1 README)
            try:
                await handler(act)
            except Exception as e:  # noqa: BLE001 — a bad handler must not kill the subscription
                self._log(f"[discord-bot] acts.v1: handler for {act.get('action')!r} raised: {e}")

        await self._client.subscribe(self._channel, on_message)
