"""Single-flight sweep guard (#637) — one runner per interval across meeting-api replicas.

At ``replicaCount>1`` every replica's FastAPI lifespan starts the same background loops with no
leader election. ``run_single_flight`` wraps a live tick body in a Postgres **session-level**
advisory lock so exactly one replica runs the body each interval; the losers skip and sleep. The
load-bearing case is ``calendar-sync`` — its external ICS/Google fetch has no other dedup, so at two
replicas it doubled the outbound requests to third-party calendar providers every interval.

Design choices (see the issue's "Along the way" forks):

* **Disjoint keyspace.** The per-user spawn/plan locks use ``pg_advisory_xact_lock(:user_id)`` on
  small user-id ints (``bot_spawn/adapters.py``, ``collector/adapters.py``). The sweep locks also use
  the single-arg form, on a **64-bit key** = ``(SWEEP_LOCK_CLASSID << 32) | crc32(loop_name)`` — the
  ``SWP\0`` namespace in the high 32 bits, so a sweep key can never collide with a small user-id key.
  (The earlier two-arg ``(classid, objid)`` form was a trap: crc32 overflows signed int4 → asyncpg
  binds it as bigint → ``pg_try_advisory_lock(int4, bigint)`` doesn't exist — the #637 witness bug.)
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

# Fixed "sweeps" namespace, folded into the HIGH 32 bits of a single 64-bit advisory key. Postgres's
# TWO-arg pg_try_advisory_lock is (int4, int4); but crc32 can exceed signed int4, so asyncpg then
# binds it as bigint → pg_try_advisory_lock(int4, bigint), a signature Postgres does NOT have — the
# #637 regression caught at the v0.12.5 witness (`function pg_try_advisory_lock(integer, bigint) does
# not exist`). The SINGLE-arg pg_try_advisory_lock(bigint) form has no such trap; packing the
# namespace into the high 32 bits keeps every sweep key disjoint from the small per-user single-arg
# locks (pg_advisory_xact_lock(:user_id), whose high 32 bits are 0).
SWEEP_LOCK_CLASSID = 0x53575000  # "SWP\0" — < 2**31, so (CLASSID<<32)|crc32 is a positive signed int8


def sweep_lock_key(loop_name: str) -> int:
    """Stable per-loop 64-bit advisory key: the SWEEP_LOCK_CLASSID namespace in the high 32 bits and
    the ``crc32(loop_name)`` name-hash in the low 32 — always a positive signed int8 (Postgres
    ``bigint``), disjoint from the small per-user single-arg locks."""
    return (SWEEP_LOCK_CLASSID << 32) | binascii.crc32(loop_name.encode("utf-8"))


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

    ``try_lock`` opens a session, takes ``pg_try_advisory_lock(cast(:key as bigint))`` on that
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
                    text("SELECT pg_try_advisory_lock(cast(:key as bigint))").bindparams(
                        bindparam("key", key)
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
                text("SELECT pg_advisory_unlock(cast(:key as bigint))").bindparams(
                    bindparam("key", key)
                )
            )
        except Exception:
            log.debug("advisory unlock best-effort failed for key %s", key, exc_info=True)
        finally:
            await db.__aexit__(None, None, None)
