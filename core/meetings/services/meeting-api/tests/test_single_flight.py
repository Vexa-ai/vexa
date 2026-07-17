"""Single-flight sweep guard (#637, A3) — offline unit lane for the cross-replica lock mechanism.

The real ``pg_try_advisory_lock`` behavior is proven in the compose/helm leg (A1); the offline suite
has no Postgres, so this lane drives the guard's contract with a hand-authored ``FakeAdvisoryLock``
(a dict-backed ``try_lock(key)->bool`` / ``unlock(key)``). Two concurrent guarded coroutines share
ONE lock and one counter: the winner's body runs, the loser's does NOT (counter == 1, not 2); after
the winner releases, a later tick by the loser DOES run (counter == 2). The negative control shows
that calling the bodies directly (no guard) yields counter == 2 — the guard, not chance, halves it.

Also asserts the disjoint-keyspace fork: the sweep lock key is a single 64-bit
``(SWEEP_LOCK_CLASSID << 32) | crc32(loop_name)`` value taken via the **single-arg**
``pg_try_advisory_lock(bigint)`` form — disjoint from the small per-user
``pg_advisory_xact_lock(:user_id)`` locks. (The earlier two-arg ``(classid, objid)`` form was the
#637 witness regression: crc32 overflowed signed int4 → bound as bigint → ``pg_try_advisory_lock(
int4, bigint)`` doesn't exist.) The REAL SQL is now exercised too: a real-Postgres conformance test
runs when ``MEETING_API_TEST_DATABASE_URL`` is set — the offline lane alone let that mismatch slip to
the live witness.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from meeting_api.sweeps.single_flight import (
    SWEEP_LOCK_CLASSID,
    run_single_flight,
    sweep_lock_key,
)


class FakeAdvisoryLock:
    """Dict-backed stand-in for a session-level Postgres advisory lock.

    ``try_lock`` returns ``True`` only if no one currently holds ``key`` (mirroring
    ``pg_try_advisory_lock`` — non-blocking, immediate ``false`` when contended). An ``await
    asyncio.sleep(0)`` yields the event loop so a second coroutine can interleave, reproducing the
    two-replicas-contend-the-same-tick race without a real DB.
    """

    def __init__(self):
        self.held: set[int] = set()
        self.try_calls: list[int] = []

    async def try_lock(self, key: int) -> bool:
        self.try_calls.append(key)
        await asyncio.sleep(0)  # yield: let the other guarded coroutine reach its own try_lock
        if key in self.held:
            return False
        self.held.add(key)
        return True

    async def unlock(self, key: int) -> None:
        self.held.discard(key)


KEY = sweep_lock_key("calendar-sync")


async def test_loser_body_does_not_run_under_contention():
    """Two guarded coroutines, one lock, one counter → exactly one body runs (counter == 1)."""
    lock = FakeAdvisoryLock()
    counter = {"n": 0}
    ran: list[bool] = []

    async def body():
        # Hold across a yield so the second coroutine's try_lock definitely lands while held.
        await asyncio.sleep(0)
        counter["n"] += 1

    async def guarded():
        ran.append(await run_single_flight(lock, KEY, body))

    await asyncio.gather(guarded(), guarded())

    assert counter["n"] == 1, "the loser's body must NOT run — single-flight, not once-per-replica"
    assert sorted(ran) == [False, True], "exactly one guarded call ran the body; the other skipped"
    assert lock.held == set(), "the winner released the lock in its finally"


async def test_loser_runs_on_a_later_uncontended_tick():
    """After the winner releases, a later tick by the (former) loser DOES run — no permanent skip."""
    lock = FakeAdvisoryLock()
    counter = {"n": 0}

    async def body():
        await asyncio.sleep(0)  # hold the lock across a yield so the contended tick truly contends
        counter["n"] += 1

    # Tick 1: contended → one body runs.
    await asyncio.gather(
        run_single_flight(lock, KEY, body),
        run_single_flight(lock, KEY, body),
    )
    assert counter["n"] == 1

    # Tick 2: uncontended (lock free) → the body runs again (the guard is not a one-shot latch).
    ran = await run_single_flight(lock, KEY, body)
    assert ran is True
    assert counter["n"] == 2


async def test_negative_control_no_guard_both_bodies_run():
    """RED analogue: call the bodies directly (guard removed) → counter == 2 (both replicas ran)."""
    counter = {"n": 0}

    async def body():
        await asyncio.sleep(0)
        counter["n"] += 1

    await asyncio.gather(body(), body())
    assert counter["n"] == 2, "without the guard both replicas' bodies run — this is the doubled work"


async def test_none_lock_degrades_to_run_the_tick():
    """Lite / no PG (session_factory is None → lock is None): the guard runs the body, never skips."""
    counter = {"n": 0}

    async def body():
        counter["n"] += 1

    ran = await run_single_flight(None, KEY, body)
    assert ran is True and counter["n"] == 1


async def test_body_exception_releases_lock():
    """A body that raises still releases the lock (finally) so the next tick can acquire it."""
    lock = FakeAdvisoryLock()

    async def boom():
        raise RuntimeError("tick blew up")

    with pytest.raises(RuntimeError):
        await run_single_flight(lock, KEY, boom)
    assert lock.held == set(), "the lock is released even when the body raises"


def test_sweep_key_disjoint_from_user_locks():
    """The 64-bit sweep key ((SWEEP_LOCK_CLASSID << 32) | crc32) can't collide with the small
    per-user single-arg locks — the namespace lives in the high 32 bits, above any user-id."""
    # Both use the single-arg pg_try/advisory_lock(bigint) space now; disjointness comes from the
    # SWP\0 namespace in the high 32 bits (user-ids are small, high bits 0).
    assert isinstance(SWEEP_LOCK_CLASSID, int)
    keys = {name: sweep_lock_key(name) for name in
            ("db-writer", "webhook-drain", "stop-reconcile", "auto-join", "calendar-sync")}
    assert len(set(keys.values())) == len(keys), "each loop hashes to a distinct objid (no collision)"
    assert sweep_lock_key("calendar-sync") == sweep_lock_key("calendar-sync"), "stable per loop name"


# ── #637 witness regression: the real advisory-lock contract (offline shape + real-PG conformance) ──

INT8_MAX = 2**63 - 1


def test_sweep_lock_key_is_valid_positive_int8():
    """Every sweep key must be a POSITIVE signed int8 (Postgres ``bigint``). The old objid was
    ``crc32(loop_name)`` alone — which overflows signed int4 for names whose hash exceeds 2**31, and
    in the two-arg form bound as bigint → ``pg_try_advisory_lock(int4, bigint)`` (the witness bug).
    Packing the namespace into the high 32 bits makes the key a well-formed bigint for every name."""
    names = ["segment-consumer", "db-writer", "webhook-drain", "stop-reconcile", "auto-join",
             "calendar-sync", "", "x" * 300, "é-loop-ÿ"]
    keys = [sweep_lock_key(n) for n in names]
    for n, k in zip(names, keys):
        assert 0 < k <= INT8_MAX, f"sweep_lock_key({n!r}) = {k} is not a positive int8"
        assert (k >> 32) == SWEEP_LOCK_CLASSID, f"{n!r}: namespace not in the high 32 bits"
    assert len(set(keys)) == len(set(names)), "distinct loop names must map to distinct keys"


async def test_pg_advisory_lock_issues_single_arg_bigint_sql():
    """``PgAdvisoryLock`` must issue the SINGLE-arg ``pg_try_advisory_lock(cast(:key as bigint))`` —
    NOT the two-arg ``(:cls, :obj)`` form that failed on real Postgres at the v0.12.5 witness.
    Needs SQLAlchemy (the lock builds the statement with it); the offline lane lacks it, which is
    part of why the two-arg form reached the live witness — so this runs wherever SQLAlchemy is."""
    pytest.importorskip("sqlalchemy")
    from meeting_api.sweeps.single_flight import PgAdvisoryLock

    executed: list[str] = []

    class _Result:
        def scalar(self):
            return True

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt, *a, **k):
            executed.append(str(stmt))
            return _Result()

    lock = PgAdvisoryLock(lambda: _Session())
    key = sweep_lock_key("calendar-sync")
    assert await lock.try_lock(key) is True
    await lock.unlock(key)
    sql = " ".join(executed).lower()
    assert "pg_try_advisory_lock(cast(:key as bigint))" in sql, f"not single-arg bigint: {sql}"
    assert "pg_advisory_unlock(cast(:key as bigint))" in sql
    assert ":cls" not in sql and ":obj" not in sql, "the two-arg (classid, objid) form must be gone"


@pytest.mark.skipif(
    not os.getenv("MEETING_API_TEST_DATABASE_URL"),
    reason="real-Postgres conformance for the advisory lock; set MEETING_API_TEST_DATABASE_URL to run",
)
async def test_pg_advisory_lock_runs_on_real_postgres():
    """Fake-conformance guard (the class the witness caught): run the ACTUAL advisory-lock SQL against
    a REAL Postgres. ``FakeAdvisoryLock`` never executes it, so the ``(int4, bigint)`` mismatch was
    invisible until a live meeting. Here ``try_lock`` executes the real ``pg_try_advisory_lock`` — the
    witness bug would raise ``UndefinedFunctionError`` on this line — and contention/release behave."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from meeting_api.sweeps.single_flight import PgAdvisoryLock

    engine = create_async_engine(os.environ["MEETING_API_TEST_DATABASE_URL"])
    try:
        sf = async_sessionmaker(engine, expire_on_commit=False)
        holder, contender = PgAdvisoryLock(sf), PgAdvisoryLock(sf)
        key = sweep_lock_key("calendar-sync")
        assert await holder.try_lock(key) is True       # runs the real SQL (bug would raise here)
        assert await contender.try_lock(key) is False    # contended: session-level lock is held
        await holder.unlock(key)
        assert await contender.try_lock(key) is True     # released -> now acquirable
        await contender.unlock(key)
    finally:
        await engine.dispose()
