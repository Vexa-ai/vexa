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


def configure(database_url: str) -> None:
    """Bind the app to a Postgres (async URL: postgresql+asyncpg://...)."""
    global _engine, _session_factory
    _engine = create_async_engine(database_url, connect_args={"statement_cache_size": 0})
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
