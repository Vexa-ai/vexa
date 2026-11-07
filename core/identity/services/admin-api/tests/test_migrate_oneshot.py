"""`python -m admin_api.migrate` — the schema's migrations path as a one-shot (F13).

meeting-api and admin-api share one schema, and only admin-api converges it (additive, never drops,
idempotent, lock-retried). Run as a step BEFORE either service's new version starts, the new
schema is in place while the old processes still serve — so both can be swapped new-beside-old
instead of stopped first. Exit 0 once converged; non-zero (and nothing half-done reported as done)
when it cannot converge."""
import asyncio

import pytest

from admin_api import migrate


def test_converges_the_one_schema_with_the_service_url_and_exits_0(monkeypatch):
    seen = {}
    monkeypatch.setenv("DB_PASSWORD", "x" * 32)
    monkeypatch.setattr(migrate, "_engine", lambda url: seen.setdefault("url", url) and "ENGINE")

    async def fake_ensure(engine, base):
        seen["engine"], seen["base"] = engine, base
    monkeypatch.setattr(migrate, "ensure_schema", fake_ensure)
    assert migrate.main() == 0
    assert seen["engine"] == "ENGINE"
    assert seen["base"] is migrate.Base
    assert seen["url"].startswith("postgresql+asyncpg://")


def test_a_schema_that_cannot_converge_exits_non_zero(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "x" * 32)
    monkeypatch.setattr(migrate, "_engine", lambda url: "ENGINE")

    async def boom(engine, base):
        raise migrate.SchemaInvariantError("duplicate rows block a unique index")
    monkeypatch.setattr(migrate, "ensure_schema", boom)
    assert migrate.main() == 1
