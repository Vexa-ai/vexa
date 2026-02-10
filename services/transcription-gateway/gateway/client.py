import logging

import redis.asyncio as aioredis

from gateway.settings import REDIS_URL

logger = logging.getLogger(__name__)


async def get_redis() -> aioredis.Redis:
    """Return an async Redis connection (decode_responses=False for binary-safe streams)."""
    return aioredis.from_url(REDIS_URL, decode_responses=False)
