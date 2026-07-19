"""#635 · meeting-api DB pool env — the single engine helper reads DB_POOL_SIZE/DB_MAX_OVERFLOW.

A2 (value · meeting-api). Deterministic: assert the kwargs meeting_api.db.engine_pool_kwargs hands
to create_async_engine (env → dict), no Postgres needed. build_engine spreads exactly this dict at
all four engine sites (__main__ + bot_spawn/collector/recordings runners).

Discriminating red→green (on HEAD 563e582 this file is RED): there is no meeting_api.db helper and
__main__.py:79 passes only pool_pre_ping=True, so env has no effect.
"""
from __future__ import annotations

import pytest

from meeting_api import db as mdb


def test_engine_pool_kwargs_reads_env(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "3")
    kw = mdb.engine_pool_kwargs()
    assert kw == {"pool_size": 7, "max_overflow": 3, "pool_pre_ping": True,
                  "connect_args": {"server_settings": {"plan_cache_mode": "force_custom_plan"}}}


def test_engine_pool_kwargs_defaults(monkeypatch):
    # pre-#635 default ceiling preserved (5 base + 10 overflow = 15) and pool_pre_ping kept.
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    kw = mdb.engine_pool_kwargs()
    assert kw == {"pool_size": 5, "max_overflow": 10, "pool_pre_ping": True,
                  "connect_args": {"server_settings": {"plan_cache_mode": "force_custom_plan"}}}


def test_build_engine_applies_pool(monkeypatch):
    # The built async engine's pool reflects the env (create_async_engine builds the pool without
    # connecting). Proves the helper's dict actually reaches the engine, not just its return value.
    # SQLAlchemy is not in the meeting-api offline gate venv (per config.v1 lazy-import discipline);
    # skip the real-engine leg there — the kwargs assertion above is the deterministic discriminator.
    pytest.importorskip("sqlalchemy")
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "3")
    engine = mdb.build_engine("postgresql+asyncpg://u:p@localhost:5432/vexa")
    assert engine.pool.size() == 7
    assert engine.pool._max_overflow == 3
