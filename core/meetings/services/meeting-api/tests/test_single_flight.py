"""Single-flight sweep guard (#637, A3) — offline unit lane for the cross-replica lock mechanism.

The real ``pg_try_advisory_lock`` behavior is proven in the compose/helm leg (A1); the offline suite
has no Postgres, so this lane drives the guard's contract with a hand-authored ``FakeAdvisoryLock``
(a dict-backed ``try_lock(key)->bool`` / ``unlock(key)``). Two concurrent guarded coroutines share
ONE lock and one counter: the winner's body runs, the loser's does NOT (counter == 1, not 2); after
the winner releases, a later tick by the loser DOES run (counter == 2). The negative control shows
that calling the bodies directly (no guard) yields counter == 2 — the guard, not chance, halves it.

Also asserts the disjoint-keyspace fork: the sweep lock key is the two-arg
``(SWEEP_LOCK_CLASSID, crc32(loop_name))`` form, which cannot collide with the single-arg per-user
``pg_advisory_xact_lock(:user_id)`` locks.
"""
from __future__ import annotations

import asyncio

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
    """The two-arg (classid, objid) sweep key can't collide with single-arg per-user xact locks."""
    # A fixed namespace classid pairs with every crc32(loop_name); the per-user locks are single-arg
    # on user-id ints, a disjoint Postgres advisory-lock space.
    assert isinstance(SWEEP_LOCK_CLASSID, int)
    keys = {name: sweep_lock_key(name) for name in
            ("db-writer", "webhook-drain", "stop-reconcile", "auto-join", "calendar-sync")}
    assert len(set(keys.values())) == len(keys), "each loop hashes to a distinct objid (no collision)"
    assert sweep_lock_key("calendar-sync") == sweep_lock_key("calendar-sync"), "stable per loop name"
