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


def active_configs(configs: Optional[list], user_id: int,
                   calendar_id: Optional[str] = None) -> list[dict]:
    """This user's LIVE calendar connections — a TOMBSTONE is never one.

    A deleted connection still gets synced by the BACKGROUND sweep (an empty feed, so ``sync_user``
    strips its sources and retires the rows only it managed), but it is not something the user can
    sync on demand and it is not part of any roster they may read back: they removed it. Measured
    live 2026-08-17 — a user whose only connections were deleted got ``200`` from
    ``POST /user/calendar/sync`` plus a ``calendars[]`` array naming every one of them, where the
    documented answer is ``404`` (no active feed) and the roster is not theirs to see.

    A PAUSED connection is NOT a tombstone: the user still has it, the panel still lists it, and
    syncing it is how a pause takes effect. Only ``deleted`` is excluded.
    """
    return [
        cfg for cfg in configs or []
        if cfg.get("user_id") == user_id
        and not cfg.get("deleted")
        and (calendar_id is None or cfg.get("calendar_id") == calendar_id)
    ]


async def run_user_sync(
    store: Any,
    cfg: dict,
    *,
    publish: Optional[Callable[[int, dict], Awaitable[None]]] = None,
    now: Optional[datetime] = None,
    rows: Optional[list] = None,
    client: Any = None,
    prev: Optional[dict] = None,
) -> dict:
    """Run one full sync for ``cfg = {user_id, ics_url, auto_join}`` → the status stamp.

    Never raises: every failure mode becomes the stamp's ``last_error`` (fail loud to the USER,
    not to the sweep). ``publish`` (optional) is called per created/updated/cancelled row so live
    lists refresh. ``rows`` (optional) is the user's meeting rows, read ONCE per tick by the
    sweep and shared across that user's connections — ``sync_user`` keeps the list current as it
    writes. ``client`` (optional) is a shared pinned httpx client for the feed fetch. ``prev``
    (optional) is this connection's LAST stamp: its ``etag``/``last_modified`` make the fetch
    conditional, so an unchanged feed answers 304 with no body and both the parse and the sync
    are skipped entirely."""
    from . import fetch_ics, parse_ics, sync_user

    user_id = cfg.get("user_id")
    moment = now or datetime.now(timezone.utc)
    stamp: dict = {"last_sync": moment.isoformat(), "last_error": None}
    prev = prev or {}
    try:
        # A tombstone (deleted) or a PAUSED (enabled: false) connection parses as an empty feed:
        # sync_user then strips this connection's sources and retires the rows only it managed.
        if cfg.get("deleted") or cfg.get("paused"):
            parsed = {"events": [], "cancelled_uids": []}
        else:
            text, fetch_err, validators = await fetch_ics(
                cfg["ics_url"], client=client,
                etag=prev.get("etag"), last_modified=prev.get("last_modified"))
            # NOTHING CHANGED: the feed answered 304 and sent no body. Skip the parse and the
            # sync entirely and keep the counts we last reported — "checked, nothing new" is a
            # different fact from "synced 14 events", and the panel could not tell them apart.
            if validators.get("not_modified"):
                stamp["unchanged"] = True
                stamp["counts"] = prev.get("counts")
                stamp["etag"] = validators.get("etag")
                stamp["last_modified"] = validators.get("last_modified")
                return stamp
            if text is None:
                stamp["last_error"] = fetch_err or "fetch failed"
                return stamp
            stamp["etag"] = validators.get("etag")
            stamp["last_modified"] = validators.get("last_modified")
            parsed = parse_ics(text, now=moment, redact_values=(cfg.get("ics_url") or "",))
        result = await sync_user(store, user_id, parsed,
                                 auto_join_default=bool(cfg.get("auto_join", True)),
                                 calendar_id=cfg.get("calendar_id"),
                                 calendar_name=cfg.get("calendar_name"),
                                 bot_name=cfg.get("bot_name"),
                                 legacy=bool(cfg.get("legacy")),
                                 rows=rows)
        stamp["counts"] = result.get("counts")
        if publish is not None:
            for entry in (result.get("created", []) + result.get("updated", [])
                          + result.get("cancelled", [])):
                await publish(user_id, entry)
    except Exception:
        stamp["last_error"] = "the feed couldn't be parsed as an ICS calendar"
    return stamp


def aggregate_stamps(stamps: list[dict]) -> dict:
    """Preserve the legacy per-user status while plural feeds keep per-connection stamps."""
    counts = {"created": 0, "updated": 0, "cancelled": 0}
    for stamp in stamps:
        for key in counts:
            counts[key] += int((stamp.get("counts") or {}).get(key, 0))
    return {
        "last_sync": stamps[-1]["last_sync"],
        "last_error": next(
            (stamp["last_error"] for stamp in stamps if stamp.get("last_error")), None
        ),
        "counts": counts,
        "calendars": stamps,
    }


async def store_stamp(redis_client: Any, user_id: int, stamp: dict,
                      calendar_id: Optional[str] = None) -> None:
    """Best-effort persist of the stamp to ``cal:sync:{user_id}`` (the panel's status read)."""
    try:
        key = f"cal:sync:{user_id}:{calendar_id}" if calendar_id else f"cal:sync:{user_id}"
        await redis_client.set(key, json.dumps(stamp))
    except Exception:
        pass


async def read_stamp(redis_client: Any, user_id: int,
                     calendar_id: Optional[str] = None) -> Optional[dict]:
    """The last stamp for a user, or ``None`` when no sync has run yet."""
    try:
        key = f"cal:sync:{user_id}:{calendar_id}" if calendar_id else f"cal:sync:{user_id}"
        raw = await redis_client.get(key)
    except Exception:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None
