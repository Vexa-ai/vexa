"""The transcript FTS index builds inside its own single-flight guard, on a real Postgres.

``CREATE INDEX CONCURRENTLY`` waits for every transaction older than its own. The guard
(``PgAdvisoryLock``) holds its session-level lock for the whole body; held on a connection sitting
``idle in transaction``, it is one of those transactions, and the build waits on its own guard for
the life of the process: the index is never built, an INVALID one is left when the process exits,
and its SHARE UPDATE EXCLUSIVE on ``transcriptions`` queues every DDL on the table (admin-api's
schema convergence).

Opt-in: set ``MEETING_API_TEST_DATABASE_URL`` (``postgresql+asyncpg://…``) to a Postgres the role
can create databases on. Each test runs in a fresh database that is dropped afterwards.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MEETING_API_TEST_DATABASE_URL to run")

INDEX = "ix_transcription_text_fts"


@pytest.fixture
async def db_url():
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    name = f"fts_guard_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        await c.execute(text(f"CREATE DATABASE {name}"))
    url = make_url(URL).set(database=name)
    eng = create_async_engine(url)
    async with eng.begin() as c:
        await c.execute(text(
            "CREATE TABLE transcriptions (id serial PRIMARY KEY, meeting_id int NOT NULL, "
            "start_time float NOT NULL, end_time float NOT NULL, text text NOT NULL)"))
        await c.execute(text(
            "INSERT INTO transcriptions (meeting_id, start_time, end_time, text) "
            "SELECT g % 12, g, g + 1, 'segment number ' || g FROM generate_series(1, 360) g"))
    await eng.dispose()
    try:
        yield url
    finally:
        async with admin.connect() as c:
            await c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        await admin.dispose()


async def _index_valid(engine):
    from sqlalchemy import text

    async with engine.connect() as c:
        return (await c.execute(text(
            "SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = :n"), {"n": INDEX})).scalar()


def _store(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore

    sf = async_sessionmaker(engine, expire_on_commit=False)
    return sf, SqlAlchemyTranscriptStore(sf)


async def _guarded_build(sf, store):
    """The production path: ``_ensure_fts_index_once`` = ``run_single_flight`` + ``PgAdvisoryLock``."""
    from meeting_api.sweeps.single_flight import PgAdvisoryLock, run_single_flight, sweep_lock_key

    out = {}

    async def body():
        out["result"] = await store.ensure_fts_index()

    ran = await run_single_flight(PgAdvisoryLock(sf), sweep_lock_key("ensure-fts-index"), body)
    return ran, out.get("result")


async def test_the_index_builds_inside_its_own_guard(db_url):
    """Red before: the build waits on its own guard's open transaction until the process exits,
    and the exit leaves the index INVALID. Green: built and valid in seconds."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(db_url)
    try:
        sf, store = _store(engine)
        task = asyncio.create_task(_guarded_build(sf, store))
        try:
            ran, result = await asyncio.wait_for(asyncio.shield(task), timeout=30)
        except asyncio.TimeoutError:
            task.cancel()                                  # the pod exits
            await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()
            engine = create_async_engine(db_url)
            pytest.fail(f"build did not finish inside its guard in 30 s; index valid after the "
                        f"process exited: {await _index_valid(engine)}")
        assert ran is True
        assert result["status"] == "created", result
        assert await _index_valid(engine) is True
    finally:
        await engine.dispose()


async def test_schema_change_on_transcriptions_is_not_held_by_the_build(db_url):
    """admin-api's convergence ALTERs ``transcriptions`` under its own lock_timeout. Red before:
    the stuck build holds SHARE UPDATE EXCLUSIVE and the ALTER times out. Green: it proceeds."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(db_url)
    try:
        sf, store = _store(engine)
        task = asyncio.create_task(_guarded_build(sf, store))
        await asyncio.sleep(1.0)
        started = time.monotonic()
        try:
            async with engine.begin() as c:
                await c.execute(text("SET LOCAL lock_timeout = '10s'"))
                await c.execute(text(
                    "ALTER TABLE transcriptions ADD COLUMN IF NOT EXISTS source varchar(32)"))
        finally:
            elapsed = time.monotonic() - started
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert elapsed < 10, f"ALTER waited {elapsed:.1f}s on the index build"
    finally:
        await engine.dispose()


async def test_an_open_transaction_bounds_the_build(db_url):
    """Another backend left ``idle in transaction``: the build gives up after lock_timeout instead
    of waiting for it, and the next attempt (the blocker gone) drops any leftover and builds."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(db_url)
    try:
        sf, store = _store(engine)
        store.FTS_INDEX_LOCK_TIMEOUT_MS = 1000
        idle = await engine.connect()
        await idle.execute(text("SELECT count(*) FROM transcriptions"))   # leaves a tx open
        started = time.monotonic()
        ran, result = await asyncio.wait_for(_guarded_build(sf, store), timeout=30)
        assert time.monotonic() - started < 10
        assert result["status"] == "failed", result
        assert "DROP INDEX CONCURRENTLY IF EXISTS" in result["by_hand"]
        assert await _index_valid(engine) is not True
        await idle.rollback()
        await idle.close()
        ran, result = await asyncio.wait_for(_guarded_build(sf, store), timeout=30)
        assert result["status"] == "created", result
        assert await _index_valid(engine) is True
    finally:
        await engine.dispose()


async def test_an_invalid_leftover_is_dropped_and_rebuilt(db_url):
    """The state a killed pod leaves behind: an INVALID index under the model name."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(db_url)
    try:
        sf, store = _store(engine)
        idle = await engine.connect()
        await idle.execute(text("SELECT 1 FROM transcriptions LIMIT 1"))
        async with engine.connect() as c:
            c = await c.execution_options(isolation_level="AUTOCOMMIT")
            await c.execute(text("SET lock_timeout = '500ms'"))
            with pytest.raises(DBAPIError):
                await c.execute(text(
                    f"CREATE INDEX CONCURRENTLY {INDEX} ON transcriptions "
                    "USING gin (to_tsvector('english', text))"))
            await c.execute(text("RESET lock_timeout"))
        await idle.rollback()
        await idle.close()
        assert await _index_valid(engine) is False
        ran, result = await asyncio.wait_for(_guarded_build(sf, store), timeout=30)
        assert result["status"] == "created", result
        assert await _index_valid(engine) is True
    finally:
        await engine.dispose()
