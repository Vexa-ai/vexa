"""Single-flight sweep guard (#637) — one runner per interval across meeting-api replicas.

At ``replicaCount>1`` every replica's FastAPI lifespan starts the same background loops with no
leader election. ``run_single_flight`` wraps a live tick body in a Postgres **session-level**
advisory lock so exactly one replica runs the body each interval; the losers skip and sleep. The
load-bearing case is ``calendar-sync`` — its external ICS/Google fetch has no other dedup, so at two
replicas it doubled the outbound requests to third-party calendar providers every interval.

Design choices (see the issue's "Along the way" forks):

* **Disjoint keyspace.** The per-user spawn/plan locks use the single-arg
  ``pg_advisory_xact_lock(:user_id)`` form on user-id ints (``bot_spawn/adapters.py``,
  ``collector/adapters.py``). The sweep locks use the **two-arg** ``pg_try_advisory_lock(classid,
  objid)`` form with a fixed ``SWEEP_LOCK_CLASSID`` namespace, so a ``crc32(loop_name)`` objid can
  never collide with a user-id single-arg key (Postgres treats the one-arg and two-arg advisory-lock
  spaces as disjoint).
* **Session-level, explicitly released.** ``pg_try_advisory_lock`` (not ``_xact_``) so the lock
  spans the whole tick and is released in a ``finally`` — and a replica that dies mid-tick drops the
  lock when its connection closes, so the other replica acquires it on the next tick (no starvation).
* **Degrade to run-the-tick.** When there is no DB session factory (Lite single-replica, or a store
  without Postgres) the guard runs the body unconditionally — it never fails closed into skipping all
  work. On a single replica the lock is always free, so every tick runs: single-replica behavior is
  unchanged.
"""
from __future__ import annotations

import binascii
import logging
from typing import Awaitable, Callable, Optional, Protocol

log = logging.getLogger("meeting_api.sweeps.single_flight")

# Fixed "sweeps" namespace for the two-arg advisory-lock form. Any stable int works; the point is
# that pairing it as the classid keeps every sweep key disjoint from the single-arg per-user locks.
SWEEP_LOCK_CLASSID = 0x53575000  # "SWP\0"


def sweep_lock_key(loop_name: str) -> int:
    """Stable per-loop objid = ``crc32(loop_name)`` (a small fixed set of name-hashes)."""
    return binascii.crc32(loop_name.encode("utf-8"))


class AdvisoryLock(Protocol):
    """The lock backend the guard drives. Production = :class:`PgAdvisoryLock`; tests inject a fake."""

    async def try_lock(self, key: int) -> bool: ...

    async def unlock(self, key: int) -> None: ...


async def run_single_flight(
    lock: Optional[AdvisoryLock],
    key: int,
    body: Callable[[], Awaitable[None]],
) -> bool:
    """Run ``body()`` at most once per interval across replicas; return whether it ran.

    * ``lock is None`` (no PG / Lite) → run ``body`` unconditionally and return ``True``.
    * lock acquired → run ``body``, release in ``finally``, return ``True``.
    * lock NOT acquired (another replica holds it this tick) → skip ``body``, return ``False``.
    """
    if lock is None:
        await body()
        return True
    if not await lock.try_lock(key):
        return False
    try:
        await body()
        return True
    finally:
        await lock.unlock(key)


class PgAdvisoryLock:
    """``AdvisoryLock`` over a SQLAlchemy-async ``session_factory``.

    ``try_lock`` opens a session, takes ``pg_try_advisory_lock(SWEEP_LOCK_CLASSID, key)`` on that
    connection, and — if acquired — HOLDS the session open so the session-scoped lock spans the tick.
    ``unlock`` releases the lock and closes the held session. ``unlock`` is best-effort: on shutdown
    the connection may already be closing, and a session-level lock releases on disconnect anyway.
    """

    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._held: dict[int, object] = {}  # key -> open AsyncSession holding the lock

    async def try_lock(self, key: int) -> bool:
        from sqlalchemy import bindparam, text

        db = self._session_factory()
        await db.__aenter__()
        try:
            got = (
                await db.execute(
                    text("SELECT pg_try_advisory_lock(:cls, :obj)").bindparams(
                        bindparam("cls", SWEEP_LOCK_CLASSID), bindparam("obj", key)
                    )
                )
            ).scalar()
        except BaseException:
            await db.__aexit__(None, None, None)
            raise
        if not got:
            await db.__aexit__(None, None, None)
            return False
        self._held[key] = db
        return True

    async def unlock(self, key: int) -> None:
        db = self._held.pop(key, None)
        if db is None:
            return
        try:
            from sqlalchemy import bindparam, text

            await db.execute(
                text("SELECT pg_advisory_unlock(:cls, :obj)").bindparams(
                    bindparam("cls", SWEEP_LOCK_CLASSID), bindparam("obj", key)
                )
            )
        except Exception:
            log.debug("advisory unlock best-effort failed for key %s", key, exc_info=True)
        finally:
            await db.__aexit__(None, None, None)
