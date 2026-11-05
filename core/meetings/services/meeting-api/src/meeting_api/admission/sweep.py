"""Bot limits, the daily job: read when bots ran, write each account's prediction to Redis.

The query reads ``user_id`` and two timestamps. It never touches ``meetings.data``, which holds
bot_logs: heavy reads of it took prod Postgres into recovery on 26 Oct 2026. So history can't say
which rows were booked; the scheduled limit learns from the shadow's own count instead (``shadow``).

Its load on prod's database is bounded by a ``created_at`` index range of 15 days, walked in 6-hour
slices with a pause between them, and a 30 s statement timeout per slice. The extra day catches rows
created shortly before their start; a row booked earlier than that is missed, which lowers its peak.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Optional

from ..obs import log_event
from .predict import Interval, predict
from .shadow import COMPUTE_KEY, PREDICTION_KEY, PREDICTION_TTL_S, FailOnce, booked_peaks

log = logging.getLogger(__name__)

HISTORY_DAYS = 14
CREATED_LEAD_DAYS = 1
SLICE = timedelta(hours=6)
SLICE_PAUSE_S = 0.2
STATEMENT_TIMEOUT = "30s"
OPEN_INTERVAL = timedelta(hours=1)  # a row with no end_time: assume about one meeting
CHECK_EVERY_S = 3600.0
COMPUTE_CLAIM_KEY = "bot_admission:compute_claim"
COMPUTE_EVERY_S = 23 * 3600  # one run a day, whichever replica or restart gets there first


def history_slice_stmt(*, created_from, created_until, started_from):
    """One slice of the ``created_at`` index. No ``meetings.data``, in any clause."""
    from sqlalchemy import select

    from ..sessions.models import Meeting

    return select(Meeting.user_id, Meeting.start_time, Meeting.end_time).where(
        Meeting.created_at >= created_from,
        Meeting.created_at < created_until,
        Meeting.start_time >= started_from,
        Meeting.platform != "browser_session",
    )


class MeetingHistory:
    """Yields pages of ``(user_id, Interval)`` over the 14-day window."""

    def __init__(self, session_factory, *, pause_s: float = SLICE_PAUSE_S) -> None:
        self._session_factory = session_factory
        self._pause_s = pause_s

    async def pages(self, now: datetime) -> AsyncIterator[list[tuple[int, Interval]]]:
        from sqlalchemy import text

        started_from = now - timedelta(days=HISTORY_DAYS)
        cursor = started_from - timedelta(days=CREATED_LEAD_DAYS)
        while cursor < now:
            until = min(cursor + SLICE, now)
            stmt = history_slice_stmt(created_from=cursor, created_until=until, started_from=started_from)
            async with self._session_factory() as db:
                await db.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
                await db.execute(text("SET LOCAL transaction_read_only = on"))
                rows = (await db.execute(stmt)).all()
            if rows:
                yield [(row.user_id, interval_of(row, now)) for row in rows]
            cursor = until
            await asyncio.sleep(self._pause_s)


def interval_of(row, now: datetime) -> Interval:
    end = row.end_time if row.end_time is not None else min(row.start_time + OPEN_INTERVAL, now)
    return Interval(start=row.start_time, end=max(end, row.start_time))


async def compute(history, redis, *, now: Optional[datetime] = None) -> Optional[dict]:
    """One run. A failed read writes nothing, so yesterday's predictions stand until their TTL."""
    at = now or datetime.now(timezone.utc).replace(tzinfo=None)
    began = time.monotonic()
    by_user: dict[int, list[Interval]] = defaultdict(list)
    rows = 0
    async for page in history.pages(at):
        rows += len(page)
        for user_id, interval in page:
            by_user[user_id].append(interval)

    for user_id, intervals in by_user.items():
        prediction = predict(intervals, booked_peaks=await booked_peaks(redis, user_id))
        await redis.set(PREDICTION_KEY.format(user_id), json.dumps(prediction.to_json()), ex=PREDICTION_TTL_S)
    summary = {"accounts": len(by_user), "rows": rows, "at": at.isoformat() + "Z",
               "duration_s": round(time.monotonic() - began, 3)}
    await redis.set(COMPUTE_KEY, json.dumps(summary), ex=PREDICTION_TTL_S)
    log_event("bot_admission_computed", audience="operator", fields=summary)
    return summary


async def daily_tick(history, redis, fail: FailOnce) -> Optional[dict]:
    """Run ``compute`` if nobody has in the last 23 hours. Logs a failure once and carries on."""
    claimed = False
    try:
        claimed = bool(await redis.set(COMPUTE_CLAIM_KEY, "1", nx=True, ex=COMPUTE_EVERY_S))
        if not claimed:
            return None
        summary = await compute(history, redis)
        fail.worked()
        return summary
    except Exception as exc:  # noqa: BLE001 — shadow only: the next check tries again
        fail.failed("daily compute", exc)
        if claimed:
            try:
                await redis.delete(COMPUTE_CLAIM_KEY)
            except Exception:  # noqa: BLE001 — the claim expires by itself
                pass
        return None
