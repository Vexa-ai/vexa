"""Async DB session wiring for the admin-api app.

The parent reads DB_* env at import time (`admin_models/database.py`). The v0.12 carve makes
the engine INJECTABLE instead: `configure(database_url)` builds the async engine + session
factory, and `get_db` is the FastAPI dependency. This lets the eval point the same app at an
ephemeral testcontainers Postgres — no global env coupling.
"""
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory: Optional[async_sessionmaker] = None


def configure(
    database_url: str,
    *,
    pool_size: Optional[int] = None,
    max_overflow: Optional[int] = None,
) -> None:
    """Bind the app to a Postgres (async URL: postgresql+asyncpg://...).

    ``pool_size`` / ``max_overflow`` steer the async engine's connection pool so an operator can fit
    a managed-Postgres ``max_connections`` ceiling (the mitigation the 2026-04-21 pool-exhaustion
    outage required; the ceiling ``deploy/db-budget.json`` audits). ``None`` for either → omit the
    kwarg and fall back to SQLAlchemy's framework default (pool_size 5 + max_overflow 10 = 15),
    exactly the pre-#635 behavior. The ``connect_args`` (asyncpg/pgbouncer statement-cache compat)
    is preserved unconditionally.
    """
    global _engine, _session_factory
    kwargs = {"connect_args": {"statement_cache_size": 0}}
    if pool_size is not None:
        kwargs["pool_size"] = pool_size
    if max_overflow is not None:
        kwargs["max_overflow"] = max_overflow
    _engine = create_async_engine(database_url, **kwargs)
    _session_factory = async_sessionmaker(bind=_engine, class_=AsyncSession, expire_on_commit=False)


def get_engine():
    if _engine is None:
        raise RuntimeError("admin_api.app.db not configured — call configure(database_url) first")
    return _engine


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("admin_api.app.db not configured — call configure(database_url) first")
    async with _session_factory() as session:
        yield session
