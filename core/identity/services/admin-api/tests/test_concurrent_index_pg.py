"""A missing non-unique index is built CONCURRENTLY after the convergence commits (docker).

The convergence transaction can hold ACCESS EXCLUSIVE on `users` and `api_tokens` until it commits
(the additive `ADD COLUMN`s of an upgrade). An index built inside it holds every `/internal/validate`
for the whole build. So a missing non-unique index on an existing table is built after the commit,
on its own autocommit connection, with CREATE INDEX CONCURRENTLY: readers and writers never wait for
it. This pins: the build comes after the commit and is concurrent; an INVALID leftover is rebuilt; a
second process holding the build lock is left to it; a long-open writer bounds the wait and the next
convergence finishes the job; the async entry point does the same."""
import asyncio
import logging
import threading
import time

from sqlalchemy import create_engine, event, text

from admin_api.schema import sync as sync_mod
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema, ensure_schema_sync
from conftest import requires_docker

pytestmark = requires_docker
INDEX = "ix_users_public_profile_handle"


def _fresh(url):
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    ensure_schema_sync(engine, Base)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (email, data) SELECT 'u' || g || '@x.test', "
            "jsonb_build_object('public_profile', jsonb_build_object('handle', 'h' || (g % 7))) "
            "FROM generate_series(1, 200) g"))
        # the 0.12 shape: no handle index, no users.org_id (its ADD COLUMN takes ACCESS EXCLUSIVE)
        conn.execute(text(f"DROP INDEX {INDEX}"))
        conn.execute(text("ALTER TABLE users DROP COLUMN org_id"))
    return engine


def _index_state(engine):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT i.indisvalid, i.indisunique FROM pg_index i JOIN pg_class c "
            "ON c.oid = i.indexrelid WHERE c.relname = :n"), {"n": INDEX}).first()


def _autocommit(engine, *stmts):
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for s in stmts:
            conn.execute(text(s))


def test_the_index_is_built_concurrently_after_the_commit(pg_url):
    engine = _fresh(pg_url)
    trail = []
    event.listen(engine, "before_cursor_execute", lambda c, cur, stmt, *a: trail.append(stmt))
    event.listen(engine, "commit", lambda conn: trail.append("<COMMIT>"))
    ensure_schema_sync(engine, Base)

    builds = [i for i, s in enumerate(trail) if INDEX in s and "CREATE" in s]
    assert builds, "the handle index was never built"
    assert all("CONCURRENTLY" in trail[i] for i in builds), [trail[i] for i in builds]
    alter = next(i for i, s in enumerate(trail) if 'ADD COLUMN "org_id"' in s)
    commit = next(i for i in range(alter, len(trail)) if trail[i] == "<COMMIT>")
    assert min(builds) > commit, "the index was built inside the convergence transaction"
    assert _index_state(engine) == (True, False)
    engine.dispose()


def test_an_invalid_leftover_is_dropped_and_rebuilt(pg_url):
    engine = _fresh(pg_url)
    ensure_schema_sync(engine, Base)
    _autocommit(engine, f"DROP INDEX {INDEX}")
    try:  # a failed concurrent build (duplicate handles under UNIQUE) leaves an INVALID index
        _autocommit(engine, f"CREATE UNIQUE INDEX CONCURRENTLY {INDEX} "
                            "ON users (((data -> 'public_profile') ->> 'handle'))")
    except Exception:  # noqa: BLE001
        pass
    assert _index_state(engine) == (False, True)
    ensure_schema_sync(engine, Base)
    assert _index_state(engine) == (True, False)
    engine.dispose()


def test_another_builder_holding_the_lock_is_left_to_it(pg_url, caplog):
    engine = _fresh(pg_url)
    holder = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": sync_mod.CONCURRENT_INDEX_LOCK_KEY})
    try:
        with caplog.at_level(logging.WARNING, logger="admin_api.schema.sync"):
            ensure_schema_sync(engine, Base)
        assert _index_state(engine) is None
        assert any("another process" in r.getMessage() for r in caplog.records)
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": sync_mod.CONCURRENT_INDEX_LOCK_KEY})
        holder.close()
    ensure_schema_sync(engine, Base)
    assert _index_state(engine) == (True, False)
    engine.dispose()


def test_a_long_open_writer_bounds_the_wait_and_the_next_pass_finishes(pg_url, caplog, monkeypatch):
    monkeypatch.setattr(sync_mod, "CONCURRENT_LOCK_TIMEOUT_MS", 500)
    engine = _fresh(pg_url)
    ensure_schema_sync(engine, Base)                      # org_id back; only the index is missing
    _autocommit(engine, f"DROP INDEX {INDEX}")
    held, release = threading.Event(), threading.Event()

    def writer():
        with engine.connect() as conn:
            tx = conn.begin()
            conn.execute(text("UPDATE users SET name = 'w' WHERE id = (SELECT min(id) FROM users)"))
            held.set()
            release.wait(30)
            tx.rollback()

    w = threading.Thread(target=writer); w.start(); assert held.wait(10)
    try:
        t0 = time.monotonic()
        with caplog.at_level(logging.ERROR, logger="admin_api.schema.sync"):
            ensure_schema_sync(engine, Base)
        took = time.monotonic() - t0
        assert took < 10, f"the convergence waited {took:.1f}s on an open writer"
        assert any(INDEX in r.getMessage() and "NOT built" in r.getMessage() for r in caplog.records)
        # a reader never queued behind the waiting build
        t0 = time.monotonic()
        with engine.connect() as conn:
            conn.execute(text("SELECT count(*) FROM users")).scalar()
        assert time.monotonic() - t0 < 1.0
    finally:
        release.set(); w.join()
    ensure_schema_sync(engine, Base)
    assert _index_state(engine) == (True, False)
    engine.dispose()


def test_the_async_entry_point_builds_it_concurrently(pg_url, pg_async_url):
    engine = _fresh(pg_url)
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    async def run():
        aengine = create_async_engine(pg_async_url)
        try:
            await ensure_schema(aengine, Base)
        finally:
            await aengine.dispose()

    asyncio.run(run())
    assert _index_state(engine) == (True, False)
    engine.dispose()
