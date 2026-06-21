"""``python -m admin_api`` — the production admin-api (P4 compose CMD).

Builds the admin-api FastAPI surface (``admin_api.app.main.create_app``), binds the injectable DB
wiring to the compose Postgres from ``DB_*`` env (the 0.11 var names), and runs the idempotent
``ensure_schema()`` convergence on startup so the identity + meeting tables exist before the first
request. uvicorn-target: ``uvicorn admin_api.__main__:app`` / ``python -m admin_api``.
"""
from __future__ import annotations

import os


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
    from .schema.models import Base
    from .schema.sync import ensure_schema

    app_db.configure(_database_url())
    app = create_app()

    @app.on_event("startup")
    async def _converge_schema() -> None:
        # Idempotent, never-drops convergence — safe to run on every boot (matches the parent's
        # ensure_schema() discipline). The async engine is the one db.configure() just bound.
        await ensure_schema(app_db.get_engine(), Base)

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
