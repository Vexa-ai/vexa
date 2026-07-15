"""Shared async-engine construction for meeting-api (#635).

meeting-api opens a Postgres pool at FOUR sites — the unified boot (``__main__.py``) and the three
standalone module runners (``bot_spawn``/``collector``/``recordings`` ``build_production_*``). This
module is the single seam that reads the pool env (``DB_POOL_SIZE`` / ``DB_MAX_OVERFLOW``) and applies
it, so every composition honors the same per-pod pool ceiling ``deploy/db-budget.json`` audits — the
knob is no longer wired in one path and silently defaulted in the others.

``pool_pre_ping=True`` is preserved (it was on every site before #635). Defaults 5/10 match
``deploy/db-budget.json``. Imported lazily by the boot paths so merely importing a service module
never pulls SQLAlchemy into the offline gate venv.
"""
from __future__ import annotations

import os


def engine_pool_kwargs() -> dict:
    """The pool kwargs every meeting-api ``create_async_engine`` call spreads.

    Deterministic (env → dict), so a unit test can assert the wiring without a live Postgres.
    """
    return {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
        "pool_pre_ping": True,
    }


def build_engine(database_url: str):
    """Construct the async engine with the env-steered pool (the single seam)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(database_url, **engine_pool_kwargs())
