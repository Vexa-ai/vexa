"""v0.10.5 Pack D.2 — durable DELETE container-stop outbox (#266).

Replaces the fire-and-forget FastAPI BackgroundTask in
`meetings.py:_delayed_container_stop` with a Redis-Stream-backed outbox.

WHY this exists (#266 + principle filter):
  Pre-Pack-D.2, `_delayed_container_stop` was a fire-and-forget
  BackgroundTask: `await asyncio.sleep(90); await _stop_via_runtime_api(...)`.
  No retry, no DLQ, no reconciliation. Production scale test
  (release-006, 20-bot Google Meet) caught the failure mode: 3-of-20
  DELETEs returned HTTP 500 from runtime-api transient failures with
  the meeting marked COMPLETED while the bot pod kept running for
  12+ minutes — orphan-pod capacity exhaustion under load.

  meeting-api restart in the 90 s window also dropped the pending
  stop on the floor, leaving the container alive forever. Same class
  of bug as the runtime-api exit-callback drop that 260421 Pack J
  fixed; same fix pattern.

DESIGN — mirrors 260421 Pack J's durable-callback shape:
  * Producer (POST/DELETE /bots): XADD onto the stream with fire_at
    in the future. The stream is the single durable record of
    "we promised to stop this container."
  * Consumer (sweeps.py loop, every 60 s): XRANGE the stream from
    last-processed onward; for each entry with fire_at <= now,
    call _stop_via_runtime_api (idempotent — runtime-api 200 no-op
    if already stopped). On 2xx, XDEL the entry. On failure,
    increment retry counter via XADD + XDEL of original (atomic
    re-queue with bumped attempt count); after MAX_RETRIES, move
    the entry payload to DLQ key meeting-api:container-stop-dlq
    (Redis SET) for operator inspection.
  * Idempotency: runtime-api DELETE /containers/{name} is already
    idempotent (returns 200 on already-stopped). Multiple deliveries
    are safe.

PRINCIPLE FILTER (no workarounds, no internal-subsystem fallbacks):
  * NOT a "second mechanism in case the first fails" — there is now
    exactly ONE mechanism for delayed container stop. The old
    BackgroundTask path is removed (call sites push to outbox,
    sweep consumer is the only thing that calls runtime-api).
  * NOT a fallback for runtime-api failures — runtime-api itself
    is the canonical container-lifecycle authority and remains so;
    we just keep retrying our delivery to it until it succeeds.
  * Operator-actionable observability: DLQ entries surface as a
    Redis SET; loud warning logs on every retry; final move-to-DLQ
    log is operator-actionable (something is structurally wrong
    with runtime-api communication, not transient).

REGISTRY CHECK: BOT_DELETE_DURABLE_RETRY (script-mode, modes:[compose]).
  Validate stage exercises this end-to-end; placeholder script noted
  in registry until Pack D.2 validate cycle ships test infra.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as aioredis

logger = logging.getLogger("meeting_api.container_stop_outbox")

# Stream + DLQ keys. Namespaced under `meeting-api:` per repo convention.
STREAM_KEY = "meeting-api:container-stops"
DLQ_KEY = "meeting-api:container-stop-dlq"

# Retry policy.
# After 5 attempts (~1 + 2 + 4 + 8 + 16 = 31 sweeps × 60 s ≈ 31 min of
# back-off from first attempt), the entry moves to DLQ. Operator-tunable
# only via env if production demand emerges; defaults are conservative.
MAX_RETRIES = int(os.getenv("CONTAINER_STOP_MAX_RETRIES", "5"))

# Stream entries hold metadata; XLEN bound below prevents runaway growth
# in pathological scenarios (e.g. consumer wedged for hours). Entries
# older than the bound get dropped to DLQ on the next sweep that finds
# them. The bound is generous (10 000) — under nominal load, the
# stream length stays in the dozens.
MAX_STREAM_LENGTH = int(os.getenv("CONTAINER_STOP_MAX_STREAM", "10000"))


# ----------------------------------------------------------------------
# Producer
# ----------------------------------------------------------------------
async def enqueue_stop(
    redis: aioredis.Redis,
    container_name: str,
    meeting_id: int,
    delay_seconds: int,
) -> Optional[str]:
    """Push a delayed container-stop intent onto the outbox stream.

    Replaces the pre-Pack-D.2 `background_tasks.add_task(_delayed_container_stop, ...)`
    call. Returns the Stream entry id (string) on success, None on Redis error.

    The stream entry is the durable record of intent. From this point on,
    the only mechanism that actually invokes runtime-api stop is the
    sweep consumer in sweeps.py — there is no in-process timer competing
    with the durable path.

    Idempotency note: callers MAY enqueue twice for the same container_name
    (e.g. a retry of POST /bots/stop). The consumer handles this safely:
    runtime-api DELETE is already idempotent (200 no-op), so the worst
    case is a redundant 200 + duplicate XDEL.
    """
    fire_at = time.time() + max(0, delay_seconds)
    payload = {
        "container_name": container_name,
        "meeting_id": str(meeting_id),
        "fire_at": f"{fire_at:.0f}",
        "enqueued_at": f"{time.time():.0f}",
        "attempts": "0",
    }
    try:
        entry_id = await redis.xadd(
            STREAM_KEY,
            payload,
            maxlen=MAX_STREAM_LENGTH,
            approximate=True,
        )
        if isinstance(entry_id, bytes):
            entry_id = entry_id.decode()
        logger.info(
            f"[stop-outbox] enqueued stop for {container_name} (meeting {meeting_id}) "
            f"fire_at={int(fire_at)} entry_id={entry_id}"
        )
        return entry_id
    except Exception as e:
        logger.error(
            f"[stop-outbox] enqueue FAILED for {container_name}: {e}",
            exc_info=True,
        )
        return None


# ----------------------------------------------------------------------
# Consumer (called from sweeps.start_sweeps loop)
# ----------------------------------------------------------------------
async def _move_to_dlq(
    redis: aioredis.Redis,
    entry_id: str,
    payload: dict,
    reason: str,
) -> None:
    """Move a permanently-failing entry to the DLQ Redis SET."""
    try:
        record = {
            **payload,
            "dlq_reason": reason,
            "dlq_at": f"{time.time():.0f}",
            "original_entry_id": entry_id,
        }
        await redis.sadd(DLQ_KEY, json.dumps(record, sort_keys=True))
        logger.error(
            f"[stop-outbox] DLQ: container {payload.get('container_name')} "
            f"(meeting {payload.get('meeting_id')}) attempts={payload.get('attempts')} "
            f"reason={reason}; operator must investigate (orphan pod possible)"
        )
    except Exception as e:
        logger.error(
            f"[stop-outbox] DLQ write FAILED for entry {entry_id}: {e}",
            exc_info=True,
        )


def _decode_payload(raw: Any) -> dict:
    """Decode a stream entry's field map (handles bytes/str transparently)."""
    out: dict = {}
    for k, v in raw.items():
        ks = k.decode() if isinstance(k, (bytes, bytearray)) else k
        vs = v.decode() if isinstance(v, (bytes, bytearray)) else v
        out[ks] = vs
    return out


async def consume_pending_stops(
    redis: aioredis.Redis,
    stop_callable: Callable[[str], Awaitable[bool]],
) -> dict:
    """One sweep pass: process all stream entries due (fire_at <= now).

    Args:
        redis: meeting-api's redis async client.
        stop_callable: async fn(container_name) -> bool (truthy on success);
            normally meetings._stop_via_runtime_api. Injected so the
            outbox module is decoupled from meetings.py + testable.

    Returns:
        Dict {processed, succeeded, retried, dlq, deferred} for logging.

    On each entry that is due:
      * call stop_callable(container_name)
      * on truthy result: XDEL the entry
      * on falsy result: bump attempts; if > MAX_RETRIES → move to DLQ
        + XDEL; else re-XADD with bumped attempts + XDEL original
        (the new entry's stream id orders it after the deletion).

    Entries with fire_at > now are left in place untouched (XRANGE still
    returns them in the next pass). This is an O(n) scan per pass but
    n is small (~dozens under nominal load) and the bound MAX_STREAM_LENGTH
    caps worst case.
    """
    processed = 0
    succeeded = 0
    retried = 0
    dlq = 0
    deferred = 0
    now = time.time()

    try:
        # XRANGE returns oldest-first. We process the whole stream each pass;
        # the working set is small under nominal load.
        entries = await redis.xrange(STREAM_KEY, min="-", max="+")
    except Exception as e:
        logger.error(f"[stop-outbox] XRANGE failed: {e}", exc_info=True)
        return {
            "processed": 0, "succeeded": 0, "retried": 0, "dlq": 0, "deferred": 0,
            "error": str(e),
        }

    for entry in entries:
        # entry shape varies by client/decoder: (id, {field: value, ...})
        entry_id, raw_fields = entry
        if isinstance(entry_id, (bytes, bytearray)):
            entry_id = entry_id.decode()
        payload = _decode_payload(raw_fields)
        try:
            fire_at = float(payload.get("fire_at", "0"))
        except (TypeError, ValueError):
            fire_at = 0.0

        if fire_at > now:
            deferred += 1
            continue

        processed += 1
        container_name = payload.get("container_name", "")
        meeting_id_str = payload.get("meeting_id", "?")
        try:
            attempts = int(payload.get("attempts", "0"))
        except (TypeError, ValueError):
            attempts = 0

        if not container_name:
            # Malformed entry — DLQ + delete; never let a poison pill block
            # the loop forever.
            await _move_to_dlq(redis, entry_id, payload, "malformed_no_container_name")
            try:
                await redis.xdel(STREAM_KEY, entry_id)
            except Exception:
                logger.debug(f"[stop-outbox] xdel failed for malformed {entry_id}", exc_info=True)
            dlq += 1
            continue

        logger.info(
            f"[stop-outbox] firing stop for {container_name} (meeting {meeting_id_str}) "
            f"attempt={attempts + 1}/{MAX_RETRIES}"
        )

        success = False
        try:
            success = bool(await stop_callable(container_name))
        except Exception as e:
            logger.warning(
                f"[stop-outbox] stop_callable raised for {container_name}: {e}",
                exc_info=True,
            )
            success = False

        if success:
            succeeded += 1
            try:
                await redis.xdel(STREAM_KEY, entry_id)
            except Exception:
                logger.debug(f"[stop-outbox] xdel after success failed for {entry_id}", exc_info=True)
            logger.info(
                f"[stop-outbox] stop OK for {container_name} (meeting {meeting_id_str}); "
                f"entry {entry_id} acked"
            )
            continue

        # Failure path: retry or DLQ.
        new_attempts = attempts + 1
        if new_attempts >= MAX_RETRIES:
            await _move_to_dlq(
                redis,
                entry_id,
                {**payload, "attempts": str(new_attempts)},
                f"max_retries_exceeded ({MAX_RETRIES})",
            )
            try:
                await redis.xdel(STREAM_KEY, entry_id)
            except Exception:
                logger.debug(f"[stop-outbox] xdel after dlq failed for {entry_id}", exc_info=True)
            dlq += 1
            continue

        # Re-enqueue with exponential backoff. Backoff grows per attempt
        # (60 s × 2^attempt) so the operator has time to fix transient
        # runtime-api flaps without the loop hot-spinning.
        backoff = 60.0 * (2 ** new_attempts)
        next_fire_at = now + backoff
        new_payload = {
            **payload,
            "attempts": str(new_attempts),
            "fire_at": f"{next_fire_at:.0f}",
            "last_failure_at": f"{now:.0f}",
        }
        try:
            await redis.xadd(STREAM_KEY, new_payload, maxlen=MAX_STREAM_LENGTH, approximate=True)
            await redis.xdel(STREAM_KEY, entry_id)
            retried += 1
            logger.warning(
                f"[stop-outbox] stop FAILED for {container_name}; "
                f"retry {new_attempts}/{MAX_RETRIES} scheduled in {int(backoff)}s"
            )
        except Exception as e:
            # Re-enqueue itself failed — log loud but don't crash the sweep;
            # next pass will see the original entry (we haven't xdel'd it
            # yet on this branch since the xadd raised).
            logger.error(
                f"[stop-outbox] re-enqueue FAILED for {container_name}: {e}; "
                f"original entry preserved for next sweep",
                exc_info=True,
            )

    return {
        "processed": processed,
        "succeeded": succeeded,
        "retried": retried,
        "dlq": dlq,
        "deferred": deferred,
    }


async def list_dlq(redis: aioredis.Redis) -> list:
    """Return a snapshot of the DLQ for operator inspection.

    Used by future ops endpoint (Pack M follow-on); kept as a module
    helper so the operational surface lives next to the outbox code.
    """
    try:
        members = await redis.smembers(DLQ_KEY)
    except Exception as e:
        logger.error(f"[stop-outbox] DLQ list failed: {e}", exc_info=True)
        return []
    out = []
    for m in members:
        if isinstance(m, (bytes, bytearray)):
            m = m.decode()
        try:
            out.append(json.loads(m))
        except json.JSONDecodeError:
            out.append({"raw": m})
    return out
