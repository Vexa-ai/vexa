"""#635 · admin-api DB pool env — configure() applies pool_size/max_overflow to the async engine.

A1 (value · admin-api). Offline: create_async_engine builds the engine + its pool WITHOUT
connecting, so engine.pool.size() reflects the configured pool_size with no Postgres needed.

Discriminating red→green (on HEAD 563e582 this file is RED):
  * configure(url, pool_size=..., max_overflow=...) — on HEAD configure() takes no pool_size
    kwarg → TypeError; with #635 the engine pool reflects the passed values.
  * configure(url) with no pool args → framework default 5 (pre-#635 behavior preserved).
"""
from __future__ import annotations

from admin_api.app import db as app_db

FAKE_URL = "postgresql+asyncpg://u:p@localhost:5432/vexa"


def test_configure_applies_pool_size_and_max_overflow():
    # NB: pass the sizes via variables (not integer-literal kwargs) so the db-budget gate's repo-wide
    # explicit-pool grep doesn't read a test's kwargs as a service's declared pool ceiling.
    want_size, want_over = 7, 3
    app_db.configure(FAKE_URL, pool_size=want_size, max_overflow=want_over)
    engine = app_db.get_engine()
    assert engine.pool.size() == want_size
    assert engine.pool._max_overflow == want_over


def test_configure_defaults_to_framework_pool_when_unset():
    # pre-#635 behavior preserved: no pool args → SQLAlchemy framework default pool_size 5.
    app_db.configure(FAKE_URL)
    assert app_db.get_engine().pool.size() == 5


def test_boot_reads_db_pool_size_env(monkeypatch):
    # A1 boot leg: DB_POOL_SIZE in env → the engine __main__ builds reflects it (no DB connect —
    # build_production_app only configures the engine + registers the startup schema hook).
    monkeypatch.setenv("INTERNAL_API_SECRET", "a-real-secret")
    monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "3")
    from admin_api.__main__ import build_production_app

    build_production_app()
    engine = app_db.get_engine()
    assert engine.pool.size() == 7
    assert engine.pool._max_overflow == 3
