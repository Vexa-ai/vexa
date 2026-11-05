"""The admin READ surface: `GET /admin/read/*`, four answers about the install, behind a key that can
do nothing else.

An operator's dashboard needs four facts from a running install: who signed up, how much the bots
were used, how each bot run ended, and whether the install is healthy. Before this module the only
key that could ask was ADMIN_API_TOKEN, which also creates users and mints their API keys — a
permanent over-grant for four read-only questions, held by a process that only ever reads. This
surface exists so that key never leaves the cluster.

THE KEY CLASS. One read key per install, presented as ``Authorization: Bearer <key>``. Only its
SHA-256 is configured (``ADMIN_READ_TOKEN_SHA256``), so the deployment holds nothing that opens
anything: a leaked chart value or pod env is not a credential. The key opens these four routes and
nothing else:

- every other admin-api route reads its own header (``X-Admin-API-Key``, ``X-API-Key``,
  ``X-Internal-Secret``) and compares it against its own secret, which the read key is not;
- a read key whose hash equals ADMIN_API_TOKEN's is refused here, so the two classes cannot be
  configured to be the same key;
- every write verb under ``/admin/read/`` answers 403, whoever asks.

THE QUERY LIMITS, on every route:

- the query runs in a READ ONLY transaction with ``SET LOCAL statement_timeout = '30s'``: Postgres
  itself refuses any write in it, and a slow plan is cut at 30 s instead of loading the database;
- a window is at most one day (``since`` no older than 24 h, ``day`` one calendar day); wider → 400;
- ``meetings.data`` is never selected whole: only the named paths below;
- at most 500 items per answer, and ONE query per request.

TIMES. Every time is UTC ISO-8601 with microseconds and a ``Z`` (``2026-11-01T10:00:00.123456Z``),
one shape on every item, so a reader that compares them as strings orders them correctly.

`grade` on a bot outcome is the product's own grading of the exit (ours · host_or_platform · user ·
normal · unknown). That grading is not in this release (meeting-api `lifecycle/outcome.py`,
Vexa-ai/vexa#1994), so every outcome is ``unknown`` with its raw ``exit_reason``; once the grading
ships it is read here, not re-derived.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db

READ_KEY_HASH_ENV = "ADMIN_READ_TOKEN_SHA256"
MAX_WINDOW = timedelta(days=1)
#: A reader asks for exactly 24 h back; by the time the request lands that is a few seconds more.
#: The slack absorbs latency and clock skew, never a second day.
WINDOW_SLACK = timedelta(minutes=10)
MAX_ITEMS = 500
STATEMENT_TIMEOUT = "30s"
PROBE_TIMEOUT_S = 2.0
#: The bot run has ended; its outcome is final.
ENDED_STATUSES = ("completed", "failed")
GRADE_UNKNOWN = "unknown"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
AUTHORIZATION = APIKeyHeader(name="Authorization", auto_error=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def read_key_hash() -> Optional[str]:
    """The configured hash, or None when the surface is not configured (or configured wrongly)."""
    value = (os.getenv(READ_KEY_HASH_ENV) or "").strip().lower()
    return value if _HEX64.match(value) else None


async def verify_read_key(authorization: Optional[str] = Security(AUTHORIZATION)) -> None:
    want = read_key_hash()
    if want is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The admin read surface is not configured.")
    admin = os.getenv("ADMIN_API_TOKEN")
    if admin and hmac.compare_digest(_sha256(admin), want):
        # Two key classes configured as one key: the read surface stays shut rather than become a
        # second door for the admin token.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The admin read key must not be the admin token.")
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(_sha256(token), want):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid or missing read key.")


def _iso(value: Optional[datetime]) -> Optional[str]:
    """A stored time (naive UTC, or aware) as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now() -> datetime:
    """Naive UTC, the shape the tables store."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _since(raw: str, now: datetime) -> datetime:
    """``since`` as naive UTC, refused (400) when it is unreadable, in the future or wider than a day."""
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="since: an ISO-8601 time")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if parsed < now - MAX_WINDOW - WINDOW_SLACK:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="since: at most 24 hours ago (one-day window)")
    if parsed > now + WINDOW_SLACK:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="since: in the future")
    return parsed


def _day(raw: Optional[str], now: datetime) -> date:
    """``day`` (YYYY-MM-DD); absent → today (UTC). Refused (400) when unreadable or in the future."""
    if raw is None or raw == "":
        return now.date()
    try:
        day = date.fromisoformat(raw.strip())
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="day: YYYY-MM-DD")
    if day > now.date():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="day: in the future")
    return day


async def _read(db: AsyncSession, sql: str, params: dict) -> list:
    """The one query of a request, in a read-only transaction cut at 30 s."""
    async with db.begin():
        await db.execute(text("SET TRANSACTION READ ONLY"))
        await db.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
        return list((await db.execute(text(sql), params)).mappings().all())


SIGNUPS_SQL = """
SELECT id AS user_id, email, created_at
  FROM users
 WHERE created_at > :since AND created_at <= :until
 ORDER BY created_at, id
 LIMIT :limit
"""

# Usage is bot time: a run counts on the day it ENDED, and only once the bot was in the call
# (start_time set). Minutes are end − start. This is not the billed figure; billing is not in this
# service's tables.
USAGE_SQL = """
SELECT m.user_id, u.email, count(*) AS meetings,
       round((coalesce(sum(extract(epoch FROM (m.end_time - m.start_time))), 0) / 60.0)::numeric, 1)
         AS minutes
  FROM meetings m
  JOIN users u ON u.id = m.user_id
 WHERE m.end_time >= :day_start AND m.end_time < :day_end
   AND m.start_time IS NOT NULL AND m.end_time >= m.start_time
 GROUP BY m.user_id, u.email
 ORDER BY minutes DESC, m.user_id
 LIMIT :limit
"""

# Two named paths of `meetings.data`, never the document.
BOT_OUTCOMES_SQL = """
SELECT id AS meeting_id, user_id, end_time AS ended_at,
       coalesce(data ->> 'completion_reason', data ->> 'reason') AS exit_reason
  FROM meetings
 WHERE end_time > :since AND end_time <= :until
   AND status IN ('completed', 'failed')
 ORDER BY end_time, id
 LIMIT :limit
"""

HEALTH_SQL = "SELECT 1"


async def _redis_ok() -> bool:
    from . import signin_links  # noqa: PLC0415 — the service Redis client, made on first use
    try:
        return bool(await signin_links._redis().ping())
    except Exception:  # noqa: BLE001 — unreachable is an answer here, not a 500
        return False


async def _runtime_status() -> str:
    """The bot pool as runtime-api reports it; ``unknown`` when this install does not name it."""
    base = (os.getenv("RUNTIME_API_URL") or "").rstrip("/")
    if not base:
        return "unknown"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
            r = await client.get(f"{base}/health")
        return "ok" if r.status_code == 200 else "down"
    except httpx.HTTPError:
        return "down"


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(verify_read_key)])

    @router.get("/admin/read/signups")
    async def read_signups(since: str = Query(...), limit: int = Query(MAX_ITEMS, ge=1, le=MAX_ITEMS),
                           db: AsyncSession = Depends(get_db)):
        now = _now()
        rows = await _read(db, SIGNUPS_SQL, {"since": _since(since, now), "until": now, "limit": limit})
        return {"items": [{"user_id": r["user_id"], "email": r["email"],
                           "created_at": _iso(r["created_at"])} for r in rows]}

    @router.get("/admin/read/usage")
    async def read_usage(day: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
        d = _day(day, _now())
        start = datetime(d.year, d.month, d.day)
        rows = await _read(db, USAGE_SQL, {"day_start": start, "day_end": start + MAX_WINDOW,
                                           "limit": MAX_ITEMS})
        return {"items": [{"user_id": r["user_id"], "email": r["email"], "day": d.isoformat(),
                           "minutes": float(r["minutes"]), "meetings": r["meetings"]} for r in rows]}

    @router.get("/admin/read/bot-outcomes")
    async def read_bot_outcomes(since: str = Query(...),
                                limit: int = Query(MAX_ITEMS, ge=1, le=MAX_ITEMS),
                                db: AsyncSession = Depends(get_db)):
        now = _now()
        rows = await _read(db, BOT_OUTCOMES_SQL, {"since": _since(since, now), "until": now,
                                                  "limit": limit})
        return {"items": [{"meeting_id": r["meeting_id"], "user_id": r["user_id"],
                           "grade": GRADE_UNKNOWN, "exit_reason": r["exit_reason"],
                           "ended_at": _iso(r["ended_at"])} for r in rows]}

    @router.get("/admin/read/health")
    async def read_health(db: AsyncSession = Depends(get_db)):
        try:
            await _read(db, HEALTH_SQL, {})
            db_status = "ok"
        except Exception:  # noqa: BLE001 — a database that does not answer is the finding
            db_status = "down"
        redis_status = "ok" if await _redis_ok() else "down"
        pool_status = await _runtime_status()
        at = _iso(_now())
        return {"checks": [{"name": name, "status": s, "checked_at": at}
                           for name, s in (("api", "ok"), ("db", db_status),
                                           ("redis", redis_status), ("bot_pool", pool_status))]}

    return router


# Registered OUTSIDE the key dependency: a write under the read prefix is refused whoever asks,
# before any key is looked at.
write_refusal = APIRouter()


@write_refusal.api_route("/admin/read/{rest:path}", methods=["POST", "PUT", "PATCH", "DELETE"],
                         include_in_schema=False)
async def refuse_write(rest: str):
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="The admin read surface is read-only.")
