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

# A dead-letter list for envelopes that exhaust the schedule or age out — so a
# permanently-failed delivery (e.g. a meeting.completed) is observable, not silently dropped.
DEAD_LETTER_KEY = "webhook:dead_letter"
DEAD_LETTER_MAX = 1000  # cap the DLQ length (keep the most recent N entries)

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


async def _deliver_one(entry: dict, transport: Transport) -> tuple[bool, Optional[int], Optional[str]]:
    """Attempt one queued delivery.

    Returns ``(success, status_code, error)``. ``success`` is True on a 2xx (or a
    permanent 4xx → stop retrying); the status_code/error are surfaced so a permanently
    failed entry can be dead-lettered with its last outcome.
    """
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
            return True, code, None
        if code >= 500 or code == 429:
            return False, code, f"HTTP {code}"  # transient — re-enqueue
        return True, code, f"HTTP {code}"  # 4xx (non-429) — permanent, drop (don't re-enqueue)
    except Exception as e:  # noqa: BLE001 — transport error is transient
        return False, None, str(e)


async def _dead_letter(
    redis: Any,
    entry: dict,
    *,
    reason: str,
    status_code: Optional[int] = None,
    error: Optional[str] = None,
    now: float,
    key: str = DEAD_LETTER_KEY,
) -> None:
    """Persist a permanently-failed envelope to the dead-letter list + log it.

    Without this an exhausted / aged-out webhook (e.g. a meeting.completed) would vanish
    with no operator visibility. The DLQ record carries the routing + last-failure metadata;
    the list is capped (LTRIM) so it can't grow unbounded.
    """
    record = {
        "url": entry.get("url"),
        "payload": entry.get("payload"),
        "label": entry.get("label", ""),
        "attempts": entry.get("attempt", 0),
        "reason": reason,
        "last_status_code": status_code,
        "last_error": error,
        "created_at": entry.get("created_at"),
        "dead_lettered_at": now,
    }
    if entry.get("metadata"):
        record["metadata"] = entry["metadata"]
    await redis.rpush(key, json.dumps(record))
    # Keep only the most recent DEAD_LETTER_MAX entries.
    await redis.ltrim(key, -DEAD_LETTER_MAX, -1)

    try:
        from ..obs import log_event
    except Exception:  # noqa: BLE001 — never let logging wiring break the drain
        log_event = None
    if log_event is not None:
        log_event(
            "webhook_dead_lettered", audience="system", level="warning",
            span="webhook.retry_drain",
            fields={
                "url": record["url"], "label": record["label"],
                "attempts": record["attempts"], "reason": reason,
                "last_status_code": status_code, "last_error": error,
                "created_at": record["created_at"],
            },
        )


async def drain_retry_queue(
    redis: Any,
    transport: Transport,
    *,
    now: Optional[float] = None,
    key: str = RETRY_QUEUE_KEY,
) -> int:
    """One worker sweep: process every READY entry once. Returns #processed.

    Entries not yet due (`next_retry_at > now`) are re-queued untouched. Entries past
    MAX_AGE, or that exhaust the schedule, are dead-lettered (not silently dropped).
    Failed-but-retryable entries get a bumped `attempt` + the next backoff and are
    re-queued. Pass `now` to drive the clock forward deterministically in the eval.

    Backoff is indexed by `attempt + 1`: `enqueue` already set the first wait to
    BACKOFF_SCHEDULE[0] (60s), so the drain schedules the *next* wait. The effective wait
    sequence a target experiences is therefore exactly the schedule (60, 300, 1800, 7200),
    and the total bounded HTTP attempts are 1 sync + len(BACKOFF_SCHEDULE) drain = 5.
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
            processed += 1  # expired — dead-letter (don't deliver)
            await _dead_letter(redis, entry, reason="max_age_exceeded", now=clock)
            continue

        if next_retry_at > clock:
            requeue.append(raw)  # not due yet
            continue

        success, status_code, error = await _deliver_one(entry, transport)
        processed += 1

        if success:
            continue
        # The first wait (BACKOFF[0]) was already applied at enqueue, so the next wait is
        # BACKOFF[attempt + 1]. When that index runs off the end the schedule is exhausted.
        next_idx = attempt + 1
        if next_idx >= len(BACKOFF_SCHEDULE):
            # exhausted — dead-letter (permanently failed)
            await _dead_letter(
                redis, entry, reason="schedule_exhausted",
                status_code=status_code, error=error, now=clock,
            )
            continue
        entry["attempt"] = next_idx
        entry["next_retry_at"] = clock + BACKOFF_SCHEDULE[next_idx]
        requeue.append(json.dumps(entry))

    if requeue:
        await redis.rpush(key, *requeue)

    return processed
