"""``python -m admin_api`` — the production admin-api (P4 compose CMD).

Builds the admin-api FastAPI surface (``admin_api.app.main.create_app``), binds the injectable DB
wiring to the compose Postgres from ``DB_*`` env (the 0.11 var names), and runs the idempotent
``ensure_schema()`` convergence on startup so the identity + meeting tables exist before the first
request. uvicorn-target: ``uvicorn admin_api.__main__:app`` / ``python -m admin_api``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

logger = logging.getLogger("admin_api.boot")

# #901: bounded initial-DB-connect retry. On a cold start the Postgres DNS name may not resolve
# yet (socket.gaierror) or the socket may refuse (ConnectionError); rather than throwing exit(3)
# on the first miss and leaning on the k8s restart loop (observed 2× in the first ~20s of the
# v0.12.17-rc.1 smoke), we retry with capped exponential backoff, then fail LOUD after the bound.
# Bounds are env-tunable but default to ~ (0.5+1+2+4+8)*→ under the 40×5s startup-probe budget.
DB_CONNECT_MAX_ATTEMPTS = int(os.getenv("DB_CONNECT_MAX_ATTEMPTS", "10"))
DB_CONNECT_BASE_DELAY = float(os.getenv("DB_CONNECT_BASE_DELAY", "0.5"))
DB_CONNECT_MAX_DELAY = float(os.getenv("DB_CONNECT_MAX_DELAY", "8.0"))

# Transient boot-time connect failures worth retrying: DNS not-yet-resolvable (socket.gaierror is
# an OSError subclass) and refused/reset sockets (ConnectionError ⊂ OSError). SQLAlchemy wraps the
# driver error in OperationalError/InterfaceError, but the underlying OSError is chained as
# __cause__, so we unwrap and match on it — narrow enough that a real auth/config error (which is
# NOT an OSError) still fails fast without burning the retry budget.
_TRANSIENT_OS_ERRORS = (socket.gaierror, ConnectionError, TimeoutError, OSError)


def _is_transient_connect_error(exc: BaseException) -> bool:
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, _TRANSIENT_OS_ERRORS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


async def _connect_with_retry(
    connect,
    *,
    max_attempts: int = DB_CONNECT_MAX_ATTEMPTS,
    base_delay: float = DB_CONNECT_BASE_DELAY,
    max_delay: float = DB_CONNECT_MAX_DELAY,
    sleep=asyncio.sleep,
):
    """Await ``connect()`` (a zero-arg coroutine fn), retrying transient connect errors with capped
    exponential backoff. Re-raises the last error LOUD once ``max_attempts`` is exhausted (#901)."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await connect()
        except BaseException as exc:  # noqa: BLE001 — re-raised below unless transient+budget left
            if attempt >= max_attempts or not _is_transient_connect_error(exc):
                if attempt >= max_attempts:
                    logger.error(
                        "admin-api DB connect failed after %d attempt(s) — giving up", attempt
                    )
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                "admin-api DB connect attempt %d/%d failed (%s: %s) — retrying in %.1fs",
                attempt, max_attempts, type(exc).__name__, exc, delay,
            )
            await sleep(delay)


def _database_url() -> str:
    """Async Postgres URL from the 0.11 DB_* env names (asyncpg driver for the async engine)."""
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    host = os.getenv("DB_HOST", "postgres")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "vexa")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


def build_production_app():
    """Configure the DB engine + assemble the app; converge the schema on startup."""
    from .app import db as app_db
    from .app.main import create_app
    from .config_preflight import preflight
    from .schema.models import Base
    from .schema.sync import ensure_schema

    # #526: refuse to boot a misconfigured deploy — a missing INTERNAL_API_SECRET makes the
    # fail-closed /internal/validate guard 503 every gateway validation hop, but the process would
    # otherwise come up green (the 2026-04-23 shape: 23 meetings failed while monitors stayed green).
    preflight()

    # #635: per-pod pool ceiling from env so an operator can fit managed Postgres' max_connections
    # (the mitigation the 2026-04-21 outage required). Defaults 5/10 match deploy/db-budget.json:7.
    app_db.configure(
        _database_url(),
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )
    app = create_app()

    @app.on_event("startup")
    async def _converge_schema() -> None:
        # Idempotent, never-drops convergence — safe to run on every boot (matches the parent's
        # ensure_schema() discipline). The async engine is the one db.configure() just bound.
        # #901: the first connect here is where a cold-start DNS race surfaces (socket.gaierror);
        # retry with bounded backoff so a not-yet-ready Postgres doesn't exit(3) into the restart
        # loop. After the bound it re-raises loud — a persistently unreachable DB still fails.
        await _connect_with_retry(lambda: ensure_schema(app_db.get_engine(), Base))

    return app


# uvicorn ``admin_api.__main__:app`` resolves this. Exposed LAZILY via PEP 562 so merely importing
# this module never wires a DB engine (SQLAlchemy/asyncpg are NOT in the offline gate venv). The app
# is built — DB configured, schema-convergence startup hook registered — only when uvicorn touches
# ``__main__.app`` at boot.
def __getattr__(name: str):
    if name == "app":
        return build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    import uvicorn

    uvicorn.run(
        build_production_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8001")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
