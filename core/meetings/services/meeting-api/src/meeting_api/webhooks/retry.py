"""The Redis-backed reliable retry queue + the crash-safe worker sweep.

Derived from the parent's `webhook_retry_worker.py`, reimplemented clean. Failed deliveries are
persisted to a Redis list (`webhook:retry_queue`); each entry carries its own `next_retry_at` +
`attempt`, and the exponential `BACKOFF_SCHEDULE`. `drain_retry_queue` is ONE worker tick (the
parent's `_process_queue` loop body) — the eval calls it directly instead of running the background
poll loop, so the test is deterministic (no sleeps).

Crash-safety (issue #520). The old drain LPOPped every entry into a process-local `requeue` list
and RPUSHed it back only at the END of the sweep, so a worker crash mid-sweep lost every popped
entry — the recovery mechanism itself dropped envelopes. This drain uses the classic Redis
**reliable-queue** pattern: each entry is atomically moved from the queue into a **processing list**
with ``RPOPLPUSH`` (never held only in process memory), delivered, then removed from the processing
list on ack / re-queued with a bumped attempt+next_retry_at on reschedule / moved to the
dead-letter list on exhaustion. A crash leaves the in-flight entry in the processing list; the next
tick's reclaim pass moves any processing entry older than a **lease** back to the queue, where it is
re-delivered. Re-delivery is at-least-once and safe to dedupe: #519 made ``event_id`` deterministic,
so a receiver dedupes a lease-reclaim redelivery on its stored ``event_id``.

Scope (stated plainly): this closes the crash-mid-sweep loss. It does NOT make the ledger survive a
full Redis restart / storage outage — the ledger is still in Redis, which the hosted platform runs
memory-only. Restart/outage durability requires persistent managed Redis (an owner/infra decision);
see the PR's follow-up note.

The redis client is async (`redis.asyncio` / `fakeredis.aioredis`). The transport is injected, same
as `WebhookSink`, so the worker drains against the fake receiver too.
"""
from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from .delivery import build_headers

RETRY_QUEUE_KEY = "webhook:retry_queue"

# The reliable-queue processing list: an entry is RPOPLPUSH'd here while in-flight, so a crash
# mid-delivery leaves it recoverable (never held only in process memory). The next tick's reclaim
# pass returns processing entries older than the lease to the retry queue.
PROCESSING_KEY = "webhook:retry_processing"

# The reclaim lease (seconds): a processing entry not resolved within this window is presumed
# orphaned by a crashed sweep and reclaimed. MUST exceed the webhook transport timeout (10s) with
# margin, so a slow-but-live delivery is never reclaimed and double-sent out from under itself.
DEFAULT_LEASE_SECONDS = 60.0

# SYSTEM post-meeting hooks retry their own queue: their targets are operator-configured
# (often cluster-internal) and their drain uses a plain (non-IP-pinned) transport — the
# per-client queue's pinned drain would block them at connect. Same entry shape + schedule.
SYSTEM_RETRY_QUEUE_KEY = "webhook:system_retry_queue"

# The system queue needs its OWN processing list. drain_retry_queue defaults
# processing_key=PROCESSING_KEY, and the system call site passes only `key=`, so
# both queues would share webhook:retry_processing. That is cross-contamination,
# not just untidiness: the reclaim pass returns an orphaned processing entry to
# the queue currently being drained, so a system hook reclaimed during a MAIN
# drain would be re-delivered over the IP-pinned client transport — the exact
# transport the comment above says blocks these (often cluster-internal) targets
# at connect. Introduced when upstream #520's reliable queue met our system queue.
SYSTEM_PROCESSING_KEY = "webhook:system_retry_processing"

# A dead-letter list for envelopes that exhaust the schedule or age out — so a
# permanently-failed delivery (e.g. a meeting.completed) is observable, not silently dropped.
DEAD_LETTER_KEY = "webhook:dead_letter"
DEAD_LETTER_MAX = 1000  # cap the DLQ length (keep the most recent N entries)

# attempt -> delay until next retry (seconds). The parent's exact schedule.
BACKOFF_SCHEDULE = [60, 300, 1800, 7200]  # 1m, 5m, 30m, 2h

MAX_AGE_SECONDS = 86400  # 24h — drop entries older than this

# Transient claim-bookkeeping keys stamped onto an entry while it sits in the processing list
# (the lease clock + a unique token so LREM matches exactly). Stripped before re-queue / delivery.
_CLAIM_AT = "_claimed_at"
_CLAIM_TOKEN = "_claim"

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

    Without this an exhausted / aged-out webhook (e.g. a meeting.completed) would vanish with no
    operator visibility. The DLQ record carries the routing + last-failure metadata; the list is
    capped (LTRIM) so it can't grow unbounded.
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


async def _reclaim_stale_processing(
    redis: Any,
    *,
    now: float,
    lease: float,
    key: str = RETRY_QUEUE_KEY,
    processing_key: str = PROCESSING_KEY,
) -> int:
    """Return orphaned in-flight entries to the retry queue. Returns #reclaimed.

    An entry sits in the processing list only while a sweep is delivering it. If a sweep crashes,
    its in-flight entry stays there; this pass (run at the start of every tick) moves any entry
    whose claim is older than ``lease`` — or that was never stamped (crash between the RPOPLPUSH and
    the stamp) — back to the queue, where the next claim re-delivers it. Entries still within their
    lease are live (a concurrent/just-started delivery) and left untouched. Re-queue is RPUSH-before-
    LREM so a crash here duplicates rather than loses (at-least-once; deduped on ``event_id``).
    """
    items = await redis.lrange(processing_key, 0, -1)
    reclaimed = 0
    for raw in items:
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            await redis.lrem(processing_key, 1, raw)  # corrupt — drop so it can't wedge the pass
            continue
        claimed_at = entry.get(_CLAIM_AT)
        if claimed_at is not None and (now - claimed_at) <= lease:
            continue  # still within the lease — a live in-flight delivery, leave it
        entry.pop(_CLAIM_AT, None)
        entry.pop(_CLAIM_TOKEN, None)
        await redis.rpush(key, json.dumps(entry))  # back to the queue FIRST (crash → dup, not loss)
        await redis.lrem(processing_key, 1, raw)
        reclaimed += 1
    return reclaimed


async def drain_retry_queue(
    redis: Any,
    transport: Transport,
    *,
    now: Optional[float] = None,
    lease: float = DEFAULT_LEASE_SECONDS,
    key: str = RETRY_QUEUE_KEY,
    processing_key: str = PROCESSING_KEY,
    dead_letter_key: str = DEAD_LETTER_KEY,
) -> int:
    """One crash-safe worker sweep: process every READY entry once. Returns #processed.

    First reclaims any orphaned in-flight entries (a crashed prior sweep). Then, for each queued
    entry, atomically claims it into the processing list (``RPOPLPUSH``), and — WITHOUT ever holding
    it only in memory — resolves it individually: entries not yet due (`next_retry_at > now`) are
    returned to the queue untouched; entries past MAX_AGE or that exhaust the schedule are
    dead-lettered; a delivered (2xx / permanent 4xx) entry is dropped from processing; a
    failed-but-retryable entry gets a bumped `attempt` + the next backoff and is re-queued. Pass
    `now` to drive the clock forward deterministically in the eval.

    Backoff is indexed by `attempt + 1`: `enqueue` already set the first wait to BACKOFF_SCHEDULE[0]
    (60s), so the drain schedules the *next* wait. The effective wait sequence a target experiences
    is therefore exactly the schedule (60, 300, 1800, 7200), and the total bounded HTTP attempts are
    1 sync + len(BACKOFF_SCHEDULE) drain = 5.

    A crash at ANY point leaves the in-flight entry in the processing list, recoverable at lease
    expiry — never held only in process memory (the loss the old LPOP-hold-RPUSH drain had).
    """
    clock = time.time() if now is None else now

    # 0. Reclaim orphaned in-flight entries from a crashed prior sweep before claiming new work.
    await _reclaim_stale_processing(
        redis, now=clock, lease=lease, key=key, processing_key=processing_key
    )

    queue_len = await redis.llen(key)
    if queue_len == 0:
        return 0

    processed = 0

    for _ in range(queue_len):
        raw = await redis.rpoplpush(key, processing_key)  # atomic claim into the processing list
        if raw is None:
            break
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            await redis.lrem(processing_key, 1, raw)  # corrupt — drop from processing
            processed += 1
            continue

        # Stamp a lease clock + unique claim token so a crash leaves a reclaimable entry, and so the
        # LREM that finalizes this entry matches EXACTLY it (the just-claimed row is at head/index 0).
        stamped_entry = dict(entry)
        stamped_entry[_CLAIM_AT] = clock
        stamped_entry[_CLAIM_TOKEN] = uuid4().hex
        stamped = json.dumps(stamped_entry)
        await redis.lset(processing_key, 0, stamped)

        created_at = entry.get("created_at", 0)
        next_retry_at = entry.get("next_retry_at", 0)
        attempt = entry.get("attempt", 0)

        if clock - created_at > MAX_AGE_SECONDS:
            processed += 1  # expired — dead-letter (don't deliver)
            await _dead_letter(redis, entry, reason="max_age_exceeded", now=clock, key=dead_letter_key)
            await redis.lrem(processing_key, 1, stamped)
            continue

        if next_retry_at > clock:
            # not due yet — return the ORIGINAL entry to the queue HEAD (LPUSH: we claim from the
            # tail via RPOPLPUSH, so a head re-queue is not re-popped within this same sweep).
            # LPUSH-before-LREM so a crash here duplicates rather than loses (deduped on event_id).
            await redis.lpush(key, raw)
            await redis.lrem(processing_key, 1, stamped)
            continue

        success, status_code, error = await _deliver_one(entry, transport)
        processed += 1

        if success:
            await redis.lrem(processing_key, 1, stamped)  # ack — drop from processing
            continue

        # The first wait (BACKOFF[0]) was already applied at enqueue, so the next wait is
        # BACKOFF[attempt + 1]. When that index runs off the end the schedule is exhausted.
        next_idx = attempt + 1
        if next_idx >= len(BACKOFF_SCHEDULE):
            # exhausted — dead-letter (permanently failed)
            await _dead_letter(
                redis, entry, reason="schedule_exhausted",
                status_code=status_code, error=error, now=clock, key=dead_letter_key,
            )
            await redis.lrem(processing_key, 1, stamped)
            continue
        entry["attempt"] = next_idx
        entry["next_retry_at"] = clock + BACKOFF_SCHEDULE[next_idx]
        # Re-queue to the HEAD (LPUSH: not re-popped this sweep, since we claim from the tail) and
        # LPUSH-before-LREM so a crash here duplicates rather than loses (deduped on event_id).
        await redis.lpush(key, json.dumps(entry))
        await redis.lrem(processing_key, 1, stamped)

    return processed
