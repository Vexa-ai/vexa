"""One user's fetch→parse→sync→stamp pass — shared by the periodic sweep AND the sync-now edge.

The terminal's "Connect your calendar" panel needs IMMEDIATE feedback (paste → result), so the
same pass the background loop runs every ``CALENDAR_SYNC_INTERVAL_S`` is also callable on demand
for a single user. Both callers get the identical stamp shape that lands in redis
``cal:sync:{user_id}``: ``{last_sync, last_error, counts?}`` — the panel renders it as-is.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional


async def run_user_sync(
    store: Any,
    cfg: dict,
    *,
    publish: Optional[Callable[[int, dict], Awaitable[None]]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Run one full sync for ``cfg = {user_id, ics_url, auto_join}`` → the status stamp.

    Never raises: every failure mode becomes the stamp's ``last_error`` (fail loud to the USER,
    not to the sweep). ``publish`` (optional) is called per created/updated/cancelled row so live
    lists refresh."""
    from . import fetch_ics, parse_ics, sync_user

    user_id = cfg.get("user_id")
    moment = now or datetime.now(timezone.utc)
    stamp: dict = {"last_sync": moment.isoformat(), "last_error": None}
    try:
        text, fetch_err = await fetch_ics(cfg["ics_url"])
        if text is None:
            stamp["last_error"] = fetch_err or "fetch failed"
            return stamp
        parsed = parse_ics(text, now=moment)
        result = await sync_user(store, user_id, parsed,
                                 auto_join_default=bool(cfg.get("auto_join", True)))
        stamp["counts"] = result.get("counts")
        if publish is not None:
            for entry in (result.get("created", []) + result.get("updated", [])
                          + result.get("cancelled", [])):
                await publish(user_id, entry)
    except Exception:
        stamp["last_error"] = "the feed couldn't be parsed as an ICS calendar"
    return stamp


async def store_stamp(redis_client: Any, user_id: int, stamp: dict) -> None:
    """Best-effort persist of the stamp to ``cal:sync:{user_id}`` (the panel's status read)."""
    try:
        await redis_client.set(f"cal:sync:{user_id}", json.dumps(stamp))
    except Exception:
        pass


async def read_stamp(redis_client: Any, user_id: int) -> Optional[dict]:
    """The last stamp for a user, or ``None`` when no sync has run yet."""
    try:
        raw = await redis_client.get(f"cal:sync:{user_id}")
    except Exception:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None
