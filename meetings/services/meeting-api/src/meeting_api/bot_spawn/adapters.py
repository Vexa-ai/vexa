"""Production adapters — the real ``MeetingRepo`` (SQLAlchemy) + ``RuntimeClient`` (runtime.v1 HTTP).

Thin translations of the ports to the concrete clients, exactly as the parent's
``meetings.request_bot`` did (SQLAlchemy INSERTs for the meeting + session; an httpx POST to the
runtime kernel's ``POST /workloads``). They carry NO test logic.

Heavy imports (SQLAlchemy, httpx) are LAZY (inside the methods / ``build_production_router``) so the
package can be imported and unit-tested with the in-memory fakes without those runtime deps in the
gate venv — which is why ``pyproject.toml`` needs no ``greenlet`` pin.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from ..sessions import new_session
from .ports import QuotaExceeded, SpawnFailed


def _row_to_dict(m) -> dict:
    return {
        "id": m.id,
        "user_id": m.user_id,
        "platform": m.platform,
        "native_meeting_id": m.platform_specific_id,
        "platform_specific_id": m.platform_specific_id,
        "status": m.status,
        "bot_container_id": m.bot_container_id,
        "start_time": m.start_time.isoformat() if m.start_time else None,
        "end_time": m.end_time.isoformat() if m.end_time else None,
        "data": m.data if isinstance(m.data, dict) else {},
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


class SqlAlchemyMeetingRepo:
    """``MeetingRepo`` over a SQLAlchemy-async ``session_factory`` (``meetings`` /
    ``meeting_sessions`` tables). Carve of the parent ``meetings.request_bot`` DB ops."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def find_active(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                    Meeting.status.in_(["requested", "joining", "awaiting_admission", "active"]),
                )
                .order_by(Meeting.created_at.desc())
            )
            m = (await db.execute(stmt)).scalars().first()
            return _row_to_dict(m) if m else None

    async def find_latest(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                )
                .order_by(Meeting.created_at.desc(), Meeting.id.desc())
            )
            m = (await db.execute(stmt)).scalars().first()
            return _row_to_dict(m) if m else None

    async def reopen_meeting(self, *, meeting_id) -> dict:
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            m = (
                await db.execute(select(Meeting).where(Meeting.id == meeting_id))
            ).scalars().first()
            m.status = "requested"
            m.end_time = None
            m.bot_container_id = None
            data = dict(m.data) if isinstance(m.data, dict) else {}
            for k in ("completion_reason", "failure_stage"):
                data.pop(k, None)
            m.data = data
            flag_modified(m, "data")
            # updated_at is set server-side by the column's onupdate=func.now() (main's pattern);
            # never write a tz-aware Python datetime into the naive column (asyncpg DataError).
            await db.commit()
            await db.refresh(m)
            return _row_to_dict(m)

    async def count_active_bots(self, *, user_id, exclude_meeting_id=None) -> int:
        from sqlalchemy import func, select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(func.count())
                .select_from(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.status.in_(["requested", "joining", "awaiting_admission", "active"]),
                    Meeting.platform != "browser_session",  # infra excluded (parent meetings.py:1091)
                )
            )
            if exclude_meeting_id is not None:
                stmt = stmt.where(Meeting.id != exclude_meeting_id)
            return int((await db.execute(stmt)).scalar() or 0)

    async def create_meeting(self, *, user_id, platform, native_meeting_id, data) -> dict:
        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            m = Meeting(
                user_id=user_id, platform=platform, platform_specific_id=native_meeting_id,
                status="requested", data=dict(data or {}),
            )
            db.add(m)
            await db.commit()
            await db.refresh(m)
            return _row_to_dict(m)

    async def create_session(self, *, meeting_id, session_uid) -> None:
        async with self._session_factory() as db:
            db.add(new_session(meeting_id, session_uid))
            await db.commit()

    async def list_sessions(self, *, meeting_id) -> list:
        from sqlalchemy import select

        from ..sessions.models import MeetingSession

        async with self._session_factory() as db:
            stmt = (
                select(MeetingSession.session_uid)
                .where(MeetingSession.meeting_id == meeting_id)
                .order_by(MeetingSession.session_start_time.asc(), MeetingSession.id.asc())
            )
            return [r for (r,) in (await db.execute(stmt)).all()]

    async def set_bot_container(self, *, meeting_id, bot_container_id) -> dict:
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            m = (
                await db.execute(select(Meeting).where(Meeting.id == meeting_id))
            ).scalars().first()
            m.bot_container_id = bot_container_id
            # updated_at is set server-side by the column's onupdate=func.now() (main's pattern);
            # never write a tz-aware Python datetime into the naive column (asyncpg DataError).
            await db.commit()
            await db.refresh(m)
            return _row_to_dict(m)


class HttpRuntimeClient:
    """``RuntimeClient`` over the runtime.v1 HTTP kernel (``POST /workloads``). 429 → QuotaExceeded;
    non-201 → SpawnFailed (parent ``_spawn_via_runtime_api``)."""

    def __init__(self, client, runtime_api_url: str):
        self._client = client
        self._url = runtime_api_url.rstrip("/")

    async def create_workload(self, spec: dict) -> dict:
        resp = await self._client.post(f"{self._url}/workloads", json=spec, timeout=30.0)
        if resp.status_code == 429:
            raise QuotaExceeded("runtime kernel: owner quota exceeded")
        if resp.status_code != 201:
            raise SpawnFailed(f"runtime kernel returned {resp.status_code}")
        return resp.json()


def build_production_router(*, database_url: Optional[str] = None, runtime_api_url: Optional[str] = None):
    """Construct the bot-spawn router with real SQLAlchemy + httpx runtime adapters from env."""
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from .router import build_router

    database_url = database_url or os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/vexa"
    )
    runtime_api_url = runtime_api_url or os.getenv("RUNTIME_API_URL", "http://runtime:8090")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    http = httpx.AsyncClient(timeout=30.0)
    return build_router(SqlAlchemyMeetingRepo(session_factory), HttpRuntimeClient(http, runtime_api_url))
