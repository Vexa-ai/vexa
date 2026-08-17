"""Fixtures: a real database, built from the real DDL, per test.

SQLite in memory with a ``StaticPool``, so every connection in a test sees the same database —
without it, an in-memory SQLite gives each connection its own empty one and the tests would pass
against nothing. Constraints are on (``PRAGMA foreign_keys=ON``), which matters: several of the
invariants under test are CHECK and UNIQUE constraints rather than Python.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from context_stack import ContextStackStore, Policy
from context_stack.models import Base
from context_stack.workspaces import create_workspace


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record):  # noqa: ANN001 - sqlalchemy event signature
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def store(session_factory):
    async with session_factory() as session:
        yield ContextStackStore(session)


@pytest.fixture
def make_workspace(store):
    """Create a workspace of any policy. Returns the async callable, not a workspace."""

    async def _make(
        workspace_id: str,
        policy: Policy,
        owner: str,
        *,
        members: tuple[str, ...] = (),
        name: str | None = None,
    ):
        return await create_workspace(
            store,
            workspace_id=workspace_id,
            name=name or workspace_id,
            address=f"{workspace_id}@vexa.ai",
            policy=policy,
            owner_subject=owner,
            owner_email=owner,
            member_emails=members,
        )

    return _make


OWNER = "owner@example.com"
MEMBER = "member@example.com"
OUTSIDER = "outsider@example.com"
