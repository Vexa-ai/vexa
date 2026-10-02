"""Hostile inputs and states — faky events, abused signals, rows that outlive their code.
The engine must degrade to TYPED failure, never crash the worker or corrupt a neighbor."""
from __future__ import annotations

import pytest

from fixtures import INVITE_REFS, drain, rig
from flows import EventType, admit, cancel, resume, retry, status, tick


def test_unknown_event_type_admits_nothing():
    db, reg, clock, world = rig()
    assert admit(db, reg, clock, source_event_id="x-1",
                 event_type="totally.unknown", subject_refs={}) == 0
    assert db.execute("SELECT COUNT(*) FROM reaction")[0][0] == 0


def test_malformed_refs_fail_typed_not_crash():
    """An envelope missing the refs a step needs → the reaction FAILS with a reason;
    the worker loop survives and other reactions are untouched."""
    db, reg, clock, world = rig()
    admit(db, reg, clock, source_event_id="bad-1", event_type="invite.received",
          subject_refs={})                                    # no meeting, no inviter — hostile
    admit(db, reg, clock, source_event_id="good-1", event_type="invite.received",
          subject_refs=INVITE_REFS)
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    drain(db, reg, clock)
    by_sid = {sid: st for sid, st in db.execute("SELECT source_event_id, status FROM reaction")}
    assert by_sid["bad-1::invite_to_summary"] == "failed"
    assert by_sid["good-1::invite_to_summary"] == "done"      # the neighbor was untouched
    bad_rid = db.execute("SELECT reaction_id FROM reaction WHERE source_event_id LIKE 'bad-1%'")[0][0]
    assert "unexpected" in (status(db, bad_rid)["reason"] or "")


def test_signals_against_wrong_states_are_refused_noops():
    db, reg, clock, world = rig()
    admit(db, reg, clock, source_event_id="s-1", event_type="invite.received",
          subject_refs=INVITE_REFS)
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    drain(db, reg, clock)
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    assert status(db, rid)["status"] == "done"
    assert resume(db, rid, "op", clock) is False              # resume a done → refused
    assert retry(db, rid, "op", clock) is False               # retry a done → refused
    assert cancel(db, rid, "op", clock) is False              # cancel a done → refused
    assert status(db, rid)["status"] == "done"                # and nothing changed
    emails_before = list(world.emails)
    drain(db, reg, clock)
    assert world.emails == emails_before                      # no zombie side effects


def test_double_resume_is_idempotent():
    db, reg, clock, world = rig()
    ev = EventType("gate.requested")
    reg.flow(name="gated", version=1, on=ev,
             steps=[reg.steps["needs_approval"], reg.steps["commit_summary"]])
    admit(db, reg, clock, source_event_id="g-1", event_type=ev.name, subject_refs={"meeting": "mg"})
    tick(db, reg, clock)
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    assert resume(db, rid, "owner", clock) is True
    assert resume(db, rid, "owner", clock) is False           # second click: refused, not doubled
    drain(db, reg, clock)
    assert world.commits == ["sha-mg"]


def test_row_for_retired_flow_version_fails_typed_not_kills_worker():
    """Worker restarts with new code that no longer registers vflow@1 — the old row must become
    a typed failure the operator can see, not a KeyError that kills every future tick."""
    db, reg, clock, world = rig()
    ev = EventType("old.requested")
    reg.flow(name="oldflow", version=1, on=ev, steps=[reg.steps["commit_summary"]])
    admit(db, reg, clock, source_event_id="o-1", event_type=ev.name, subject_refs={"meeting": "mo"})
    # simulate the redeploy: a fresh registry WITHOUT oldflow
    _, reg2, _, _ = rig()
    assert tick(db, reg2, clock) is True                      # must not raise
    rows = db.execute("SELECT status, reason FROM reaction WHERE source_event_id LIKE 'o-1%'")
    assert rows[0][0] == "failed" and "unknown flow" in rows[0][1]
    # the worker is still alive for other work
    admit(db, reg2, clock, source_event_id="n-1", event_type="invite.received",
          subject_refs=INVITE_REFS)
    world2 = None  # reg2 has its own world; just ensure ticking proceeds without exception
    assert tick(db, reg2, clock) in (True, False)


def test_row_for_renamed_step_fails_typed():
    db, reg, clock, world = rig()
    ev = EventType("stepgone.requested")
    reg.flow(name="stepgone", version=1, on=ev, steps=[reg.steps["commit_summary"]])
    admit(db, reg, clock, source_event_id="sg-1", event_type=ev.name, subject_refs={"meeting": "ms"})
    db.execute("UPDATE reaction SET step = 'no_such_step' WHERE source_event_id LIKE 'sg-1%'")
    assert tick(db, reg, clock) is True                       # must not raise
    st = db.execute("SELECT status, reason FROM reaction WHERE source_event_id LIKE 'sg-1%'")[0]
    assert st[0] == "failed" and "unknown step" in st[1]


def test_clock_regression_does_not_wedge():
    """now() jumping BACKWARD (ntp, restarts) must not strand a claimed row forever."""
    db, reg, clock, world = rig()
    admit(db, reg, clock, source_event_id="c-1", event_type="invite.received",
          subject_refs=INVITE_REFS)
    tick(db, reg, clock)                                      # runs create_meeting
    clock._t -= 3600                                          # time goes backward an hour
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    drain(db, reg, clock, max_ticks=2000)                     # drain jumps forward again
    assert db.execute("SELECT status FROM reaction")[0][0] == "done"


def test_cancel_mid_wait_stops_all_future_effects():
    db, reg, clock, world = rig()
    admit(db, reg, clock, source_event_id="k-1", event_type="invite.received",
          subject_refs=INVITE_REFS)
    for _ in range(4):
        tick(db, reg, clock)                                  # up to await_start wait
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    assert cancel(db, rid, "owner", clock, reason="changed my mind")
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    drain(db, reg, clock)
    assert status(db, rid)["status"] == "cancelled"
    assert world.bots_dispatched == []                        # the bot never went
    assert not any(a.startswith("sha-") for _, a in world.emails)
