"""``python -m meeting_signals`` — the signals service: preflight, then the runner and /health."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from .config_preflight import preflight
from .health import serve
from .keys import KeySource
from .chat_feed import ChatBriefReader, ChatFeed
from .meeting_feed import BriefReader, MeetingFeed
from .runner import Runner
from .wake import FlowsEmitter


#: the longest BLOCK any source passes to XREAD(GROUP) — the socket must out-wait it
BLOCK_MS = 1000


def redis_client(url: str):
    """The service redis: a read that times out (a slow or busy redis) is retried with backoff
    instead of failing the runner's turn; the socket out-waits every blocking read."""
    import redis.asyncio as aioredis
    from redis.asyncio.retry import Retry
    from redis.backoff import ExponentialBackoff
    from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

    return aioredis.from_url(
        url, decode_responses=True, health_check_interval=10,
        socket_timeout=10.0, socket_connect_timeout=5.0, retry_on_timeout=True,
        retry=Retry(ExponentialBackoff(cap=2.0, base=0.1), 3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError])


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    preflight()

    me = os.environ.get("SIGNALS_INSTANCE") or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    r = redis_client(os.environ.get("REDIS_URL", ""))
    agent, secret = os.environ.get("SIGNALS_AGENT_API_URL", ""), os.environ.get("INTERNAL_API_SECRET", "")
    runner = Runner(
        r, [MeetingFeed(r, BriefReader(agent, secret), consumer=me),
            ChatFeed(r, ChatBriefReader(agent, secret), consumer=me)],
        KeySource(os.environ.get("VEXA_SIGNALS_BROKER_URL", ""), os.environ.get("VEXA_SIGNALS_BROKER_KEY_FILE", "")),
        FlowsEmitter(os.environ.get("VEXA_FLOWS_API_URL", ""), secret),
        me=me, usd_per_mtok=float(os.environ.get("SIGNALS_USD_PER_MTOK") or 2.0),
    )
    server = await serve(int(os.environ.get("SIGNALS_PORT") or 8310), runner.health)
    logging.getLogger("signals").info("signals up as %s", me)
    async with server:
        await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
