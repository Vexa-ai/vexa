"""The deterministic fixture matrix: happy paths, duplicate delivery, crash boundaries,
lease recovery, block/signal, escalation — the PRD §13 rows, offline."""
from __future__ import annotations

from fixtures import INVITE_REFS, drain, rig
from flows import admit, reclaim, resume, retry, status, tick
from flows.loop import MAX_ATTEMPTS


def _admit_invite(db, reg, clock, world, sid="ev-1"):
    n = admit(db, reg, clock, source_event_id=sid, event_type="invite.received",
              subject_refs=INVITE_REFS)
    return n


def _complete_meeting(world, mid="m-1"):
    world.meeting_state[mid] = {"completed": True, "final": True}


def test_happy_path_end_to_end():
    db, reg, clock, world = rig()
    assert _admit_invite(db, reg, clock, world) == 1
    _complete_meeting(world)
    drain(db, reg, clock)
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    st = status(db, rid)
    assert st["status"] == "done"
    assert world.meetings_created == ["m-1"]
    assert world.bots_dispatched == ["m-1"]
    assert world.commits == ["sha-m-1"]
    # inviter confirm + 2 inside-domain summaries; the outsider got nothing
    assert ("anna@bank.com", "confirm") in world.emails
    assert ("anna@bank.com", "sha-m-1") in world.emails
    assert ("ben@bank.com", "sha-m-1") in world.emails
    assert not any(r == "eve@other.io" for r, _ in world.emails)


def test_duplicate_delivery_is_noop():
    db, reg, clock, world = rig()
    assert _admit_invite(db, reg, clock, world, "ev-dup") == 1
    for _ in range(5):
        assert _admit_invite(db, reg, clock, world, "ev-dup") == 0
    _complete_meeting(world)
    drain(db, reg, clock)
    assert len(db.execute("SELECT 1 FROM reaction")) == 1
    assert world.emails.count(("anna@bank.com", "sha-m-1")) == 1


def test_two_flows_one_event_admit_independently():
    db, reg, clock, world = rig()
    _complete_meeting(world)
    n = admit(db, reg, clock, source_event_id="c-1", event_type="meeting.completed",
              subject_refs=INVITE_REFS)
    assert n == 1                        # post_meeting matches meeting.completed
    drain(db, reg, clock)
    assert world.commits == ["sha-m-1"]


def test_crash_after_effect_before_advance_never_repeats_effect():
    db, reg, clock, world = rig()
    _admit_invite(db, reg, clock, world)
    world.fail_after_effect.add("commit_summary")     # crash AFTER the commit exists
    _complete_meeting(world)
    drain(db, reg, clock)
    st = status(db, db.execute("SELECT reaction_id FROM reaction")[0][0])
    assert st["status"] == "done"
    assert world.commits == ["sha-m-1"], "the crash retried the step but the commit is single"


def test_transient_failures_backoff_then_succeed():
    db, reg, clock, world = rig()
    world.fail_next["dispatch_bot"] = 3
    _admit_invite(db, reg, clock, world)
    _complete_meeting(world)
    drain(db, reg, clock)
    assert world.bots_dispatched == ["m-1"]
    assert status(db, db.execute("SELECT reaction_id FROM reaction")[0][0])["status"] == "done"


def test_permanent_failure_is_typed_and_operator_retryable():
    db, reg, clock, world = rig()
    world.fail_next["process_transcript"] = MAX_ATTEMPTS + 5
    _admit_invite(db, reg, clock, world)
    _complete_meeting(world)
    drain(db, reg, clock)
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    st = status(db, rid)
    assert st["status"] == "failed" and "injected fault" in st["reason"]
    assert retry(db, rid, actor="operator", clock=clock)      # new attempt, causally linked
    drain(db, reg, clock)
    assert status(db, rid)["status"] == "done"
    assert world.commits == ["sha-m-1"]


def test_expired_lease_is_reclaimed():
    db, reg, clock, world = rig()
    _admit_invite(db, reg, clock, world)
    from flows import claim
    r = claim(db, clock)                              # worker takes it… and dies
    assert r is not None
    assert tick(db, reg, clock) is False              # nobody else can claim while leased
    clock.advance(120)                                # lease (90s) expires
    assert reclaim(db, clock) == 1
    _complete_meeting(world)
    drain(db, reg, clock)
    assert status(db, r.reaction_id)["status"] == "done"


def test_block_waits_for_signal_and_deadline_escalates():
    db, reg, clock, world = rig()
    flow = reg.flow(name="approval_flow", version=1,
                    on=__import__("flows").EventType("thing.requested"),
                    steps=[reg.steps["needs_approval"], reg.steps["commit_summary"]])
    admit(db, reg, clock, source_event_id="t-1", event_type="thing.requested",
          subject_refs={"meeting": "m-9"})
    tick(db, reg, clock)
    rid = db.execute("SELECT reaction_id FROM reaction WHERE flow='approval_flow'")[0][0]
    assert status(db, rid)["status"] == "blocked"
    assert resume(db, rid, actor="owner", clock=clock)        # the human says go
    drain(db, reg, clock)
    assert status(db, rid)["status"] == "done"
    # and the escalation path: a second one nobody approves
    admit(db, reg, clock, source_event_id="t-2", event_type="thing.requested",
          subject_refs={"meeting": "m-10"})
    tick(db, reg, clock)
    clock.advance(86401)
    from flows import escalate
    assert escalate(db, clock) == 1
