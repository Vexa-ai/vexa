"""The deterministic fixture: a FakeClock, a sqlite DB, the fake world, both flows registered.
Every test and the storm build on this — zero domains, zero sleeps, zero network."""
from __future__ import annotations

from flows import FakeClock, SqliteDB, admit, escalate, reclaim, tick
from flows_defs.defs import register_flows
from flows_steps.fakes import FakeWorld, build_registry


def rig():
    world = FakeWorld()
    reg = build_registry(world)
    register_flows(reg)
    db = SqliteDB()
    clock = FakeClock()
    return db, reg, clock, world


INVITE_REFS = {
    "meeting": "m-1", "inviter": "anna@bank.com",
    "participants": ["anna@bank.com", "ben@bank.com", "eve@other.io"],
    "start_time": 1_003_600.0,           # clock start + 3600
}


def drain(db, reg, clock, *, max_ticks: int = 500) -> int:
    """Run until nothing is due. FakeClock advances to the earliest next_run_at when idle,
    so waits pass instantly and a livelock fails the test via max_ticks."""
    ticks = 0
    for _ in range(max_ticks):
        reclaim(db, clock)
        escalate(db, clock)
        if tick(db, reg, clock):
            ticks += 1
            continue
        rows = db.execute(
            "SELECT MIN(next_run_at) FROM reaction WHERE status IN ('admitted','retrying')")
        nxt = rows[0][0]
        if nxt is None:
            return ticks                 # nothing runnable, nothing scheduled → drained
        clock._t = max(clock._t, nxt)    # jump to the next due moment
    raise AssertionError("drain did not converge — livelock or runaway retries")
