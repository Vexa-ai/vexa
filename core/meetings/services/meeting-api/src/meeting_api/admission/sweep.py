"""Bot limits, the daily job: read when bots ran, write each account's prediction to Redis.

The query reads timestamps, ``user_id`` and whether the row was booked. Nothing else leaves the
table: no transcripts, no ``meetings.data`` beyond the one key tested for presence.

Its load on prod's database is bounded three ways: the ``created_at`` index range, a page of
``PAGE_ROWS`` rows with a pause between pages, and a 30 s statement timeout per page. A booked row is
created when the calendar sync first sees it, up to 14 days before it runs (``DEFAULT_HORIZON_DAYS``
in ``calendar_sync/service.py``), so the range on ``created_at`` reaches 14 days further back than the
14 days of ``start_time`` it keeps.
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
from .shadow import COMPUTE_KEY, PREDICTION_KEY, PREDICTION_TTL_S, FailOnce

log = logging.getLogger(__name__)

HISTORY_DAYS = 14
BOOKED_AHEAD_DAYS = 14  # calendar_sync.service.DEFAULT_HORIZON_DAYS
PAGE_ROWS = 1000
PAGE_PAUSE_S = 0.2
STATEMENT_TIMEOUT = "30s"
MAX_OPEN_INTERVAL = timedelta(hours=6)  # a row with no end_time: updated_at, within reason
CHECK_EVERY_S = 3600.0
COMPUTE_CLAIM_KEY = "bot_admission:compute_claim"
COMPUTE_EVERY_S = 23 * 3600  # one run a day, whichever replica or restart gets there first


def history_page_stmt(*, created_from, created_until, started_from, after, limit):
    """One page, ordered along the ``created_at`` index. ``after`` is the last (created_at, id)."""
    from sqlalchemy import and_, or_, select

    from ..sessions.models import Meeting

    stmt = (
        select(
            Meeting.id,
            Meeting.user_id,
            Meeting.created_at,
            Meeting.start_time,
            Meeting.end_time,
            Meeting.updated_at,
            Meeting.data["scheduled_at"].astext.isnot(None).label("booked"),
        )
        .where(
            Meeting.created_at >= created_from,
            Meeting.created_at < created_until,
            Meeting.start_time >= started_from,
            Meeting.platform != "browser_session",
        )
        .order_by(Meeting.created_at, Meeting.id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(
            Meeting.created_at >= after[0],
            or_(Meeting.created_at > after[0], and_(Meeting.created_at == after[0], Meeting.id > after[1])),
        )
    return stmt


class MeetingHistory:
    """Yields pages of ``(user_id, Interval)`` over the 14-day window."""

    def __init__(self, session_factory, *, page_rows: int = PAGE_ROWS, pause_s: float = PAGE_PAUSE_S) -> None:
        self._session_factory = session_factory
        self._page_rows = page_rows
        self._pause_s = pause_s

    async def pages(self, now: datetime) -> AsyncIterator[list[tuple[int, Interval]]]:
        from sqlalchemy import text

        started_from = now - timedelta(days=HISTORY_DAYS)
        after = None
        while True:
            stmt = history_page_stmt(
                created_from=started_from - timedelta(days=BOOKED_AHEAD_DAYS), created_until=now,
                started_from=started_from, after=after, limit=self._page_rows,
            )
            async with self._session_factory() as db:
                await db.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
                await db.execute(text("SET LOCAL transaction_read_only = on"))
                rows = (await db.execute(stmt)).all()
            if not rows:
                return
            yield [(row.user_id, interval_of(row, now)) for row in rows]
            if len(rows) < self._page_rows:
                return
            after = (rows[-1].created_at, rows[-1].id)
            await asyncio.sleep(self._pause_s)


def interval_of(row, now: datetime) -> Interval:
    if row.end_time is not None:
        end = row.end_time
    else:
        end = min(row.updated_at or row.start_time, row.start_time + MAX_OPEN_INTERVAL, now)
    return Interval(start=row.start_time, end=max(end, row.start_time), booked=bool(row.booked))


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
        await redis.set(
            PREDICTION_KEY.format(user_id), json.dumps(predict(intervals).to_json()), ex=PREDICTION_TTL_S,
        )
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
