"""
Background worker that retries failed webhooks from the Redis queue.

Polls the ``webhook:retry_queue`` list every POLL_INTERVAL seconds. Each
entry carries its own ``next_retry_at`` timestamp and exponential backoff
schedule. Entries older than MAX_AGE_SECONDS (24 h) are dropped.

Usage (inside bot-manager startup)::

    from shared_models.webhook_retry_worker import start_retry_worker, stop_retry_worker

    # on startup
    asyncio.create_task(start_retry_worker(redis_client))

    # on shutdown
    await stop_retry_worker()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, List, Optional

import httpx

from .webhook_delivery import RETRY_QUEUE_KEY, build_headers

logger = logging.getLogger(__name__)

# Backoff schedule: attempt -> delay until next retry (seconds)
BACKOFF_SCHEDULE = [
    60,       # after 1st worker attempt: retry in 1 min
    300,      # 5 min
    1800,     # 30 min
    7200,     # 2 h
]

MAX_AGE_SECONDS = 86400  # 24 hours
POLL_INTERVAL = 30       # seconds between queue polls

_stop_event: Optional[asyncio.Event] = None


async def _deliver_one(entry: dict) -> bool:
    """Attempt to deliver a single queued webhook. Returns True on success."""
    url = entry["url"]
    payload = entry["payload"]
    webhook_secret = entry.get("webhook_secret")
    label = entry.get("label", "")

    # Re-build headers (recalculates timestamp/signature)
    payload_bytes = json.dumps(payload).encode()
    headers = build_headers(webhook_secret, payload_bytes)

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, content=payload_bytes, headers=headers, timeout=30.0)
            if resp.status_code < 300:
                logger.info(f"[retry-worker] Delivered queued webhook to {url} [{label}]: {resp.status_code}")
                return True
            if resp.status_code >= 500 or resp.status_code == 429:
                logger.warning(f"[retry-worker] Server error from {url} [{label}]: {resp.status_code}")
                return False
            # 4xx (except 429) — treat as permanent failure, don't retry
            logger.warning(f"[retry-worker] Permanent failure for {url} [{label}]: {resp.status_code}. Dropping.")
            return True  # return True so it's not re-enqueued
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.warning(f"[retry-worker] Transient error for {url} [{label}]: {e}")
        return False
    except Exception as e:
        logger.error(f"[retry-worker] Unexpected error delivering to {url} [{label}]: {e}")
        return False


async def _process_queue(redis_client: Any) -> int:
    """Process all ready entries in the retry queue.

    Returns the number of entries processed (delivered or dropped).
    """
    now = time.time()
    queue_len = await redis_client.llen(RETRY_QUEUE_KEY)
    if queue_len == 0:
        return 0

    processed = 0
    requeue: List[str] = []

    for _ in range(queue_len):
        raw = await redis_client.lpop(RETRY_QUEUE_KEY)
        if raw is None:
            break

        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"[retry-worker] Corrupt queue entry, dropping: {raw!r:.200}")
            processed += 1
            continue

        created_at = entry.get("created_at", 0)
        next_retry_at = entry.get("next_retry_at", 0)
        attempt = entry.get("attempt", 0)

        # Drop entries older than MAX_AGE
        if now - created_at > MAX_AGE_SECONDS:
            logger.warning(
                f"[retry-worker] Dropping webhook to {entry.get('url')} — "
                f"exceeded max age ({MAX_AGE_SECONDS}s). label={entry.get('label')}"
            )
            processed += 1
            continue

        # Not ready yet — put back
        if next_retry_at > now:
            requeue.append(raw)
            continue

        # Attempt delivery
        success = await _deliver_one(entry)
        processed += 1

        if not success:
            # Re-enqueue with bumped attempt and next backoff
            entry["attempt"] = attempt + 1
            backoff_idx = min(attempt, len(BACKOFF_SCHEDULE) - 1)
            entry["next_retry_at"] = now + BACKOFF_SCHEDULE[backoff_idx]
            requeue.append(json.dumps(entry))
            logger.info(
                f"[retry-worker] Re-enqueued webhook to {entry.get('url')} — "
                f"attempt {entry['attempt']}, next retry in {BACKOFF_SCHEDULE[backoff_idx]}s"
            )

    # Put deferred/re-queued entries back
    if requeue:
        await redis_client.rpush(RETRY_QUEUE_KEY, *requeue)

    return processed


async def start_retry_worker(redis_client: Any) -> None:
    """Run the retry worker loop. Call via ``asyncio.create_task()``.

    The worker runs until ``stop_retry_worker()`` is called or the task
    is cancelled.
    """
    global _stop_event
    _stop_event = asyncio.Event()

    logger.info("[retry-worker] Starting webhook retry worker")
    while not _stop_event.is_set():
        try:
            processed = await _process_queue(redis_client)
            if processed:
                logger.info(f"[retry-worker] Processed {processed} queue entries")
        except Exception as e:
            logger.error(f"[retry-worker] Error processing queue: {e}", exc_info=True)

        # Wait for POLL_INTERVAL or until stopped
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=POLL_INTERVAL)
            break  # stop_event was set
        except asyncio.TimeoutError:
            pass  # normal — poll again

    logger.info("[retry-worker] Webhook retry worker stopped")


async def stop_retry_worker() -> None:
    """Signal the retry worker to stop."""
    global _stop_event
    if _stop_event is not None:
        _stop_event.set()
