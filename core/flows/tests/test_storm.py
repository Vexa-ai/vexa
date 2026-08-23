"""THE STORM — adversarial, randomized runs against the engine in full isolation.

Each round builds a fresh rig, admits a fleet of events (with duplicate deliveries), then drives
N logical workers whose every tick may be struck by: injected step faults, crashes AFTER the
effect, worker death mid-lease (simulated by claiming and abandoning), random reconciler timing,
random meeting completions, random operator cancel/retry, and time jumps. After the chaos budget
is spent it drains to quiescence and asserts the INVARIANTS — the things that must hold under any
interleaving:

  I1  one reaction per (event, flow) — duplicates created nothing
  I2  every effect exactly once per target: meetings, bots, commits, per-recipient emails
  I3  no summary email exists without its commit (order held under every crash)
  I4  terminal states only: done / failed / cancelled — nothing leased, nothing forgotten
  I5  every failed reaction carries a reason (typed failure, not a mystery)
  I6  operator retry after the storm converges the failed to done — with still-exactly-once effects

Seeds are printed on failure: `STORM_SEED=n pytest tests/test_storm.py` reproduces a run exactly.
"""
from __future__ import annotations

import os
import random

from fixtures import drain, rig
from flows import admit, cancel, claim, reclaim, resume, retry, status, tick

ROUNDS = int(os.environ.get("STORM_ROUNDS", "25"))
EVENTS = 8
CHAOS_TICKS = 400


def _mk_refs(i: int) -> dict:
    return {"meeting": f"m-{i}", "inviter": f"host{i}@bank.com",
            "participants": [f"host{i}@bank.com", f"p{i}@bank.com", f"x{i}@other.io"],
            "start_time": 1_000_000.0 + 600 * i}


def _storm_once(seed: int) -> None:
    rnd = random.Random(seed)
    db, reg, clock, world = rig()

    # admit a fleet, every event delivered 1–4 times
    for i in range(EVENTS):
        for _ in range(rnd.randint(1, 4)):
            admit(db, reg, clock, source_event_id=f"ev-{i}", event_type="invite.received",
                  subject_refs=_mk_refs(i))

    cancelled: set[str] = set()
    for _ in range(CHAOS_TICKS):
        move = rnd.random()
        if move < 0.08:                                   # a meeting completes
            world.meeting_state[f"m-{rnd.randrange(EVENTS)}"] = {"completed": True, "final": True}
        elif move < 0.16:                                 # transient fault lands on a step
            step = rnd.choice(["create_meeting", "dispatch_bot", "process_transcript",
                               "commit_summary", "email_participants", "confirm_by_email"])
            world.fail_next[step] = world.fail_next.get(step, 0) + 1
        elif move < 0.22:                                 # crash AFTER an effect
            world.fail_after_effect.add(rnd.choice(
                ["create_meeting", "dispatch_bot", "commit_summary", "email_participants"]))
        elif move < 0.28:                                 # a worker claims and DIES
            claim(db, clock)
        elif move < 0.31:                                 # operator cancels a random live reaction
            rows = db.execute("SELECT reaction_id FROM reaction "
                              "WHERE status IN ('admitted','retrying')")
            if rows:
                rid = rnd.choice(rows)[0]
                cancel(db, rid, actor="storm", clock=clock, reason="storm cancel")
                cancelled.add(rid)
        elif move < 0.36:                                 # time lurches forward
            clock.advance(rnd.choice([1, 30, 90, 600, 3600]))
        elif move < 0.42:                                 # reconciler runs at a random moment
            reclaim(db, clock)
        elif move < 0.46:                                 # HOSTILE: malformed / unknown events
            kind = rnd.random()
            if kind < 0.4:
                admit(db, reg, clock, source_event_id=f"junk-{rnd.randrange(99)}",
                      event_type="no.such.event", subject_refs={})
            elif kind < 0.7:
                admit(db, reg, clock, source_event_id=f"malformed-{rnd.randrange(3)}",
                      event_type="invite.received", subject_refs={})   # missing every ref
            else:
                rows = db.execute("SELECT reaction_id FROM reaction")
                if rows:  # signal abuse: random verb at a random reaction in whatever state
                    rid = rnd.choice(rows)[0]
                    rnd.choice([resume, retry])(db, rid, "storm-abuser", clock)
        else:                                             # an honest worker tick
            tick(db, reg, clock)

    # end of chaos: complete every meeting, clear faults, drain to quiescence
    for i in range(EVENTS):
        world.meeting_state[f"m-{i}"] = {"completed": True, "final": True}
    world.fail_next.clear()
    world.fail_after_effect.clear()
    clock.advance(4000)
    drain(db, reg, clock, max_ticks=3000)

    # ── invariants ──────────────────────────────────────────────────────────
    rows = db.execute("SELECT reaction_id, source_event_id, status, reason FROM reaction "
                      "WHERE source_event_id NOT LIKE 'malformed-%'")
    assert len(rows) == EVENTS, f"I1: {len(rows)} reactions for {EVENTS} events"        # I1

    for rid, sid, st, reason in rows:
        assert st in ("done", "failed", "cancelled"), f"I4: {sid} ended {st}"           # I4
        if st == "failed":
            assert reason, f"I5: failed {sid} without reason"                           # I5

    assert len(world.meetings_created) == len(set(world.meetings_created))              # I2
    assert len(world.bots_dispatched) == len(set(world.bots_dispatched))                # I2
    assert len(world.commits) == len(set(world.commits))                                # I2
    assert len(world.emails) == len(set(world.emails)), "I2: duplicate email"           # I2

    for (rcpt, artifact) in world.emails:                                               # I3
        if artifact.startswith("sha-"):
            assert artifact in world.commits, f"I3: email {rcpt} cites missing {artifact}"

    # I6: the operator retries every failed reaction; everything not cancelled converges done
    for rid, sid, st, _ in rows:
        if st == "failed":
            assert retry(db, rid, actor="storm-op", clock=clock)
    drain(db, reg, clock, max_ticks=3000)
    for rid, sid, st, why in db.execute(
            "SELECT reaction_id, source_event_id, status, reason FROM reaction"):
        if sid.startswith("malformed-"):
            assert st in ("failed", "cancelled") and (st != "failed" or why), \
                f"hostile event must end typed, got {st}"
            continue
        if rid in cancelled:
            assert st == "cancelled"
        else:
            assert st == "done", f"I6: {sid} stuck {st} after operator retry"
    assert len(world.emails) == len(set(world.emails)), "I2 after retries"
    assert len(world.commits) == len(set(world.commits)), "I2 after retries"


def test_storm():
    fixed = os.environ.get("STORM_SEED")
    seeds = [int(fixed)] if fixed else list(range(ROUNDS))
    for seed in seeds:
        try:
            _storm_once(seed)
        except AssertionError as e:
            raise AssertionError(f"[STORM_SEED={seed}] {e}") from e


def test_engine_restart_with_durable_db_never_repeats_effects():
    """The gap the live witness exposed (duplicate confirmation emails): the storm never
    restarted the ENGINE. With a durable DB, a restarted engine must resume — same reactions,
    receipts honored, zero repeated effects. (The witness runner's per-process throwaway sqlite
    is the anti-pattern this test pins: state must outlive the process.)"""
    import os, tempfile
    from flows import SqliteDB, admit, tick, reclaim
    from flows_defs.defs import register_flows
    from flows_steps.fakes import FakeWorld, build_registry
    from fixtures import INVITE_REFS, drain
    from flows import FakeClock

    path = tempfile.mktemp(suffix=".db")
    try:
        world = FakeWorld()
        reg = build_registry(world); register_flows(reg)
        db, clock = SqliteDB(path), FakeClock()
        admit(db, reg, clock, source_event_id="r-1", event_type="invite.received",
              subject_refs=INVITE_REFS)
        for _ in range(3):
            tick(db, reg, clock)                       # a few steps happen, then the ENGINE dies

        # restart: NEW process state (fresh world/registry/clock) + the SAME database
        world2 = FakeWorld()
        reg2 = build_registry(world2); register_flows(reg2)
        db2 = SqliteDB(path)
        clock2 = FakeClock(clock.now())
        # duplicate delivery arrives after the restart too
        assert admit(db2, reg2, clock2, source_event_id="r-1", event_type="invite.received",
                     subject_refs=INVITE_REFS) == 0     # dedup survives the restart
        world2.meeting_state["m-1"] = {"completed": True, "final": True}
        drain(db2, reg2, clock2)
        rows = db2.execute("SELECT status FROM reaction")
        assert [r[0] for r in rows] == ["done"]
        # effects done before the crash are NOT repeated after it: receipts carried them over,
        # so the restarted world performed only the remaining steps
        pre = {(r, a) for (r, a) in world.emails}
        post = {(r, a) for (r, a) in world2.emails}
        assert not (pre & post), f"repeated effects across restart: {pre & post}"
    finally:
        try: os.unlink(path)
        except OSError: pass
