"""unit_faults.py — why a chat's queue is not moving, recorded where the chat can read it (P18).

A spawn the runtime refused used to leave nothing behind but a 500. Anything already on the chat's
inbox then sat under "queued behind the current turn" until its hour ran out, because nothing it
could read said the turn in front of it would never come (the founder, 0.13.2 demo stack: *"this
fails our 'fail loud' principle"*).

So when a dispatch's spawn fails, agent-api records the TYPED fault (`shared.runtime_fault`) under
``unit:{id}:fault`` — one key per chat unit, and agent-api is its only writer. Two readers:

  * the pending list (`api_shared.inbox_pending`) marks each queued row `blocked` by it, so the
    person sees which dependency stopped the queue and can retry, instead of a queue that looks
    alive;
  * the chat's SSE relay (`routers/chats.py`) answers an attach to that unit with an ``error``
    event carrying it, rather than a stream that waits for a worker nobody started.

It is NOT written onto the unit's out-stream: ``unit:{id}:out`` has exactly one writer, the worker
(P23, `architecture.calm.json` node `out-stream`). The relay composes the two for the reader.

EVIDENCE, NOT A LATCH (P21). The record carries the worker's inbox cursor as it stood when the fault
happened. A worker that takes anything afterwards moves that cursor, and from then on the record is
history, not a block — so a runtime that was briefly unreachable while a warm worker kept working
never paints that worker's queue as stuck. A spawn that succeeds clears it outright.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from shared import runtime_fault, units
from shared.runtime_fault import RuntimeFault

logger = logging.getLogger("agent_api.unit_faults")

#: As long as a queued row is shown at all (`api_shared.INBOX_PENDING_MAX_AGE_SEC`): a block that
#: outlived every row it could explain would explain nothing.
FAULT_TTL_SEC = 3600


def fault_key(unit_id: str) -> str:
    return f"unit:{unit_id}:fault"


def record(r, unit_id: str, fault: dict) -> None:
    """Record ``fault`` (a ``RuntimeFault.as_dict()``) as the reason this unit's queue is stopped.
    Best-effort: the refusal itself is already on its way to the caller, and must not be replaced by
    a bookkeeping error."""
    if r is None:
        return
    try:
        cursor = r.get(units.inbox_cursor_key(unit_id)) or ""
        r.set(fault_key(unit_id), json.dumps({**fault, "at": round(time.time(), 3), "cursor": cursor}),
              ex=FAULT_TTL_SEC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record the runtime fault for unit=%s: %s", unit_id, exc)


def clear(r, unit_id: str) -> None:
    """The unit spawned: nothing is blocking its queue any more."""
    if r is None:
        return
    try:
        r.delete(fault_key(unit_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not clear the runtime fault for unit=%s: %s", unit_id, exc)


def live(r, unit_id: str) -> Optional[dict]:
    """The fault still blocking this unit's queue, or None.

    Live means recorded AND the worker has taken nothing since — its inbox cursor is where it was
    when the fault happened. Fails soft: a record that cannot be read blocks nothing."""
    if r is None:
        return None
    try:
        raw = r.get(fault_key(unit_id))
        if not raw:
            return None
        fault = json.loads(raw)
        if not isinstance(fault, dict):
            return None
        if (r.get(units.inbox_cursor_key(unit_id)) or "") != (fault.get("cursor") or ""):
            return None                      # the worker moved since: history, not a block
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read the runtime fault for unit=%s: %s", unit_id, exc)
        return None
    return {k: v for k, v in fault.items() if k != "cursor"}


def live_at(redis_url: "str | None", unit_id: str) -> Optional[dict]:
    """:func:`live`, for a reader that holds a URL rather than a client (the routes)."""
    if not redis_url:
        return None
    try:
        import redis

        return live(redis.from_url(redis_url, decode_responses=True), unit_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read the runtime fault for unit=%s: %s", unit_id, exc)
        return None


def answer(fault: RuntimeFault) -> dict:
    """The JSON body agent-api answers a refused dispatch with — ``detail`` stays a sentence for
    every client that reads only that, and ``fault`` is the typed half a client can render."""
    return {"detail": fault.sentence(), "fault": fault.as_dict()}


def error_event(fault: dict) -> dict:
    """The chat stream's ``error`` event for a recorded fault (the SSE relay's half, see above)."""
    return {"type": "error", "message": runtime_fault.sentence(fault), "fault": fault}
