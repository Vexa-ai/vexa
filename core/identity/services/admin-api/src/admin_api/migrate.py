"""`python -m admin_api.migrate` — converge the schema once, then exit (the migrations path).

admin-api owns the one schema meeting-api also reads and writes. Its convergence is additive,
never drops, is idempotent and retries a lock it cannot take (`schema/sync.py`). admin-api still
converges on every boot; this entry point runs the same convergence as a separate step BEFORE a new
admin-api or meeting-api starts, so the new schema exists while the old processes still serve and
both services can be swapped new-beside-old rather than stopped first (compose `admin-schema-init`,
the chart's migrations Job). Exit 0 once converged, 1 when it cannot be (an invariant a duplicate
row blocks, a database that never answers): the deploy then swaps nothing.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from .__main__ import _connect_with_retry, _database_url
from .schema.errors import SchemaInvariantError
from .schema.models import Base
from .schema.sync import ensure_schema

log = logging.getLogger("admin_api.migrate")


def _engine(url: str):
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415
    return create_async_engine(url, pool_size=1, max_overflow=0)


async def _run() -> None:
    engine = _engine(_database_url())
    try:
        await _connect_with_retry(lambda: ensure_schema(engine, Base))
    finally:
        dispose = getattr(engine, "dispose", None)
        if dispose is not None:
            await dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[admin-schema] %(message)s")
    try:
        asyncio.run(_run())
    except SchemaInvariantError as exc:
        log.error("STOP: the schema cannot converge: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 — any failure means nothing may be swapped
        log.error("STOP: schema convergence failed (%s: %s)", type(exc).__name__, exc)
        return 1
    log.info("schema converged (additive, idempotent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
