"""Env-gated REAL-Postgres fixtures for the meeting-api suite (#1068).

Why this file exists
--------------------
The tenancy boundary the product actually ships is **SQL**: ``list_meetings`` builds an access
UNION (owner / transcript-share viewer / bound-workspace member) and ANDs any JSONB ``@>``
containment filter onto the outer query; every owner-scoped mutation re-selects
``WHERE user_id AND platform AND platform_specific_id … FOR UPDATE`` before it writes. CI, however,
only ever asserted that boundary against ``InMemoryTranscriptStore`` — hand-written Python
predicates (``m["user_id"] == user_id``). A regression in the real SQL (an accidental ``OR``, a
dropped ``user_id`` predicate, a filter applied outside the union) keeps every fake-based test green
while leaking across tenants in production. #1068.

So: a fixture that stands the REAL schema up in a REAL Postgres and hands back the PRODUCTION
adapter (``SqlAlchemyTranscriptStore``), so the thing CI guards is the thing that ships.

The gate convention (mirrored from ``test_single_flight.py``)
------------------------------------------------------------
Exactly the shape ``test_pg_advisory_lock_runs_on_real_postgres`` already established:

    @requires_pg            # == @pytest.mark.skipif(not os.getenv("MEETING_API_TEST_DATABASE_URL"))

With ``MEETING_API_TEST_DATABASE_URL`` unset — the default gate run, ``pnpm gate:python`` — every
consumer SKIPS with a readable reason and the suite stays green with no database anywhere.

Running the gated lane
----------------------
The gate venv deliberately has no SQLAlchemy/asyncpg (``pyproject.toml`` carries no ``greenlet``
pin precisely because the fakes never import them), so the drivers are supplied ad-hoc::

    docker run --rm -d --name mapi-pg -e POSTGRES_PASSWORD=test -p 5433:5432 postgres:16
    MEETING_API_TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:5433/postgres \
      uv run --with 'sqlalchemy[asyncio]>=2' --with asyncpg --with greenlet \
      pytest -q tests/test_pg_isolation.py

A plain ``postgresql://`` URL is accepted too — it is normalized to the asyncpg driver below. If
the env var IS set but the drivers are missing, the fixture ``importorskip``s rather than erroring,
so a half-configured environment reports "skipped, install X" instead of a collection blow-up.

Isolation + teardown
--------------------
Each pytest SESSION gets its OWN uniquely-named schema (``mapi_test_<12 hex>``) inside whatever
database the URL points at. The engine pins ``search_path`` to that schema, so
``Base.metadata.create_all`` builds the ``meetings`` / ``transcriptions`` / ``meeting_sessions``
tables — GIN indexes, partial unique index and all — entirely inside it. ``public`` is never
touched, so pointing the env var at a scratch database with other content is safe.

Teardown is trapped twice: the session fixture drops the schema in a ``finally:`` (so a failing or
erroring test still drops it), and the same idempotent drop is registered with ``atexit`` as a
belt-and-braces path for aborted runs (``-x``, KeyboardInterrupt, an exception during collection).
The drop is ``DROP SCHEMA IF EXISTS … CASCADE`` and runs at most once. Should even that fail, the
schema name is printed as a warning so an operator can drop the orphan by hand rather than
discovering it months later.
"""
from __future__ import annotations

import asyncio
import atexit
import os
import uuid
import warnings

import pytest

PG_URL_ENV = "MEETING_API_TEST_DATABASE_URL"

#: The suite-wide gate. Identical in shape to ``test_single_flight.py``'s real-PG conformance
#: guard, so there is one convention for "this test needs a real Postgres", not two.
requires_pg = pytest.mark.skipif(
    not os.getenv(PG_URL_ENV),
    reason=f"real-Postgres coverage; set {PG_URL_ENV} to run",
)

#: The tables the production adapter reads/writes, in FK-safe truncation order.
_TABLES = ("transcriptions", "meeting_sessions", "meetings")


def _async_url(raw: str) -> str:
    """Normalize a Postgres URL to the asyncpg driver ``create_async_engine`` needs.

    ``postgresql+asyncpg://…`` passes through untouched (the form the existing advisory-lock test
    already expects); bare ``postgres://`` / ``postgresql://`` are rewritten so a URL copied from
    ``psql`` or a compose file works without editing.
    """
    if "+" in raw.split("://", 1)[0]:
        return raw
    scheme, sep, rest = raw.partition("://")
    if not sep:
        return raw
    return f"postgresql+asyncpg://{rest}" if scheme in ("postgres", "postgresql") else raw


async def _create_schema(url: str, schema: str) -> None:
    """CREATE SCHEMA + ``Base.metadata.create_all`` inside it, on a throwaway engine."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from meeting_api.sessions.models import Base

    admin = create_async_engine(url, poolclass=_null_pool())
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        await admin.dispose()

    engine = create_async_engine(url, poolclass=_null_pool(), **_search_path(schema))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def _drop_schema(url: str, schema: str) -> None:
    """Idempotent ``DROP SCHEMA IF EXISTS … CASCADE`` on a fresh connection.

    Deliberately its own engine: the test's engine may be in an unusable state (a poisoned
    transaction, a connection killed mid-statement) exactly when teardown matters most.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url, poolclass=_null_pool())
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        await engine.dispose()


def _null_pool():
    from sqlalchemy.pool import NullPool

    return NullPool


def _search_path(schema: str) -> dict:
    """Engine kwargs pinning every connection's ``search_path`` to the temp schema.

    asyncpg applies ``server_settings`` per connection, so unqualified table names in the models
    (``__tablename__ = "meetings"``) resolve inside the temp schema for DDL and DML alike — no
    ``schema=`` annotation on the production models, which must stay exactly as they ship.
    """
    return {"connect_args": {"server_settings": {"search_path": schema}}}


@pytest.fixture(scope="session")
def pg_schema() -> str:
    """A uniquely-named schema holding the real meeting-api tables, dropped when the session ends.

    Synchronous on purpose. It owns its own short-lived event loops via ``asyncio.run`` so it never
    borrows (or outlives) pytest-asyncio's per-test loop — a session-scoped *async* fixture would
    bind an engine to a loop that is closed before teardown runs.
    """
    pytest.importorskip("sqlalchemy", reason="real-Postgres lane needs SQLAlchemy (async)")
    pytest.importorskip("asyncpg", reason="real-Postgres lane needs the asyncpg driver")
    pytest.importorskip("greenlet", reason="SQLAlchemy's async bridge needs greenlet")

    url = _async_url(os.environ[PG_URL_ENV])
    schema = f"mapi_test_{uuid.uuid4().hex[:12]}"
    dropped = {"done": False}

    def _drop_once() -> None:
        if dropped["done"]:
            return
        dropped["done"] = True
        try:
            asyncio.run(_drop_schema(url, schema))
        except Exception as exc:  # never mask the real test failure — report the orphan instead
            warnings.warn(
                f"{PG_URL_ENV}: failed to drop temp schema {schema!r} ({exc!r}). "
                f'Drop it by hand: DROP SCHEMA IF EXISTS "{schema}" CASCADE;',
                stacklevel=1,
            )

    # Belt: fires on an aborted run (-x, KeyboardInterrupt, a collection-time raise) where pytest
    # never reaches the finalizer below. Braces: the `finally` on the happy/failing path.
    atexit.register(_drop_once)
    asyncio.run(_create_schema(url, schema))
    try:
        yield schema
    finally:
        _drop_once()
        atexit.unregister(_drop_once)


@pytest.fixture
async def pg_store(pg_schema):
    """The PRODUCTION ``SqlAlchemyTranscriptStore`` over the temp schema — not a fake.

    Yields ``(store, session_factory)``: the store is what the routes call, the session factory is
    for seeding rows and for reading the table back to prove a refused write left no trace.

    Tables are truncated before each test (``RESTART IDENTITY CASCADE``) so row ids are predictable
    and tests do not leak into one another. ``redis_client=None`` keeps the read path pure-Postgres —
    the live in-flight-segment merge is a separate concern with its own fakeredis coverage.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore

    url = _async_url(os.environ[PG_URL_ENV])
    engine = create_async_engine(url, **_search_path(pg_schema))
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")
            )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        yield SqlAlchemyTranscriptStore(session_factory, redis_client=None), session_factory
    finally:
        await engine.dispose()
