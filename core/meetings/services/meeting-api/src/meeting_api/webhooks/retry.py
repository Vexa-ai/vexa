"""The fakeredis-backed retry queue + the worker sweep.

Derived from the parent's `webhook_retry_worker.py`, reimplemented clean. Failed
deliveries are persisted to a Redis list (`webhook:retry_queue`); each entry carries its
own `next_retry_at` + `attempt`, and the exponential `BACKOFF_SCHEDULE`. `drain_retry_queue`
is ONE worker tick (the parent's `_process_queue` loop body) — the eval calls it directly
instead of running the background poll loop, so the test is deterministic (no sleeps).

The redis client is async (`redis.asyncio` / `fakeredis.aioredis`). The transport is
injected, same as `WebhookSink`, so the worker drains against the fake receiver too.
"""
from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .delivery import build_headers

RETRY_QUEUE_KEY = "webhook:retry_queue"

# attempt -> delay until next retry (seconds). The parent's exact schedule.
BACKOFF_SCHEDULE = [60, 300, 1800, 7200]  # 1m, 5m, 30m, 2h

MAX_AGE_SECONDS = 86400  # 24h — drop entries older than this

Transport = Callable[[str, bytes, Dict[str, str]], Awaitable[Any]]


class RetryQueue:
    """A thin async wrapper over the Redis list that holds failed deliveries."""

    def __init__(self, redis: Any, key: str = RETRY_QUEUE_KEY):
        self.redis = redis
        self.key = key

    async def enqueue(
        self,
        url: str,
        envelope: Dict[str, Any],
        webhook_secret: Optional[str] = None,
        label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> None:
        ts = time.time() if now is None else now
        entry = {
            "url": url,
            "payload": envelope,
            "webhook_secret": webhook_secret,
            "label": label,
            "attempt": 0,
            "next_retry_at": ts + BACKOFF_SCHEDULE[0],  # first retry after the 1st backoff
            "created_at": ts,
        }
        if metadata:
            entry["metadata"] = metadata
        await self.redis.rpush(self.key, json.dumps(entry))

    async def depth(self) -> int:
        return await self.redis.llen(self.key)


async def _deliver_one(entry: dict, transport: Transport) -> bool:
    """Attempt one queued delivery. True = success (or permanent 4xx → stop retrying)."""
    url = entry["url"]
    envelope = entry["payload"]
    secret = entry.get("webhook_secret")
    payload_bytes = json.dumps(envelope).encode()
    ts = str(int(time.time()))
    headers = build_headers(secret, payload_bytes, timestamp=ts)
    try:
        resp = await transport(url, payload_bytes, headers)
        code = getattr(resp, "status_code", 0)
        if code < 300:
            return True
        if code >= 500 or code == 429:
            return False  # transient — re-enqueue
        return True  # 4xx (non-429) — permanent, drop (don't re-enqueue)
    except Exception:  # noqa: BLE001 — transport error is transient
        return False


async def drain_retry_queue(
    redis: Any,
    transport: Transport,
    *,
    now: Optional[float] = None,
    key: str = RETRY_QUEUE_KEY,
) -> int:
    """One worker sweep: process every READY entry once. Returns #processed.

    Entries not yet due (`next_retry_at > now`) are re-queued untouched. Entries past
    MAX_AGE are dropped. Failed-but-retryable entries get a bumped `attempt` + the next
    backoff and are re-queued, until the schedule is exhausted. Pass `now` to drive the
    clock forward deterministically in the eval (no real sleeps).
    """
    clock = time.time() if now is None else now
    queue_len = await redis.llen(key)
    if queue_len == 0:
        return 0

    processed = 0
    requeue: List[str] = []

    for _ in range(queue_len):
        raw = await redis.lpop(key)
        if raw is None:
            break
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            processed += 1  # corrupt — drop
            continue

        created_at = entry.get("created_at", 0)
        next_retry_at = entry.get("next_retry_at", 0)
        attempt = entry.get("attempt", 0)

        if clock - created_at > MAX_AGE_SECONDS:
            processed += 1  # expired — drop
            continue

        if next_retry_at > clock:
            requeue.append(raw)  # not due yet
            continue

        success = await _deliver_one(entry, transport)
        processed += 1

        if success:
            continue
        if attempt >= len(BACKOFF_SCHEDULE):
            continue  # exhausted — drop (permanently failed)
        entry["attempt"] = attempt + 1
        backoff_idx = min(attempt, len(BACKOFF_SCHEDULE) - 1)
        entry["next_retry_at"] = clock + BACKOFF_SCHEDULE[backoff_idx]
        requeue.append(json.dumps(entry))

    if requeue:
        await redis.rpush(key, *requeue)

    return processed
