"""The real-workflow loopback fixture: one invite fact in, the whole product loop out —
including the world answering back through the webhook door."""
from __future__ import annotations

from flows import admit, status
from loopback import drain_with_world, loopback_rig

REFS = {"meeting": "m-42", "inviter": "anna@bank.com",
        "participants": ["anna@bank.com", "ben@bank.com", "eve@other.io"],
        "start_time": 1_003_600.0}


def test_full_loopback_round_trip_from_one_fact():
    db, reg, clock, world = loopback_rig()
    admit(db, reg, clock, source_event_id="inv-42", event_type="invite.received", subject_refs=REFS)
    drain_with_world(db, reg, clock, world)

    flows_end = {f: st for _, f, st in
                 [(r[0], r[1], r[2]) for r in db.execute("SELECT reaction_id, flow, status FROM reaction")]}
    assert flows_end == {"invite_to_bot": "done", "post_meeting": "done"}

    # the chain, in order: confirm before the bot, bot before the summary, summary before the mails
    assert ("anna@bank.com", "confirm") in world.emails
    assert world.bots_dispatched == ["m-42"]
    assert world.commits == ["sha-m-42"]
    assert ("anna@bank.com", "sha-m-42") in world.emails
    assert ("ben@bank.com", "sha-m-42") in world.emails
    assert not any(r == "eve@other.io" for r, _ in world.emails)         # outsider silent

    # the webhook was DELIVERED 3× (transport retries) yet produced ONE post_meeting reaction
    assert len(db.execute("SELECT 1 FROM reaction WHERE flow='post_meeting'")) == 1
    # and exactly one summary email per recipient despite everything
    assert len(world.emails) == len(set(world.emails))


def test_loopback_with_faults_still_single_effects():
    """Faults on both sides of the loop: dispatch fails twice, commit crashes after effect —
    the round trip still converges with exactly-once effects."""
    db, reg, clock, world = loopback_rig()
    world.fail_next["dispatch_bot"] = 2
    world.fail_after_effect.add("commit_summary")
    admit(db, reg, clock, source_event_id="inv-9", event_type="invite.received",
          subject_refs={**REFS, "meeting": "m-9"})
    drain_with_world(db, reg, clock, world)
    assert world.bots_dispatched == ["m-9"]
    assert world.commits == ["sha-m-9"]
    ends = [st for _, st in db.execute("SELECT flow, status FROM reaction")]
    assert ends == ["done", "done"]


def test_two_meetings_interleaved_loopbacks():
    db, reg, clock, world = loopback_rig()
    admit(db, reg, clock, source_event_id="inv-a", event_type="invite.received",
          subject_refs={**REFS, "meeting": "m-a", "start_time": 1_002_000.0})
    admit(db, reg, clock, source_event_id="inv-b", event_type="invite.received",
          subject_refs={**REFS, "meeting": "m-b", "start_time": 1_004_000.0})
    drain_with_world(db, reg, clock, world)
    assert sorted(world.bots_dispatched) == ["m-a", "m-b"]
    assert sorted(world.commits) == ["sha-m-a", "sha-m-b"]
    assert all(st == "done" for _, st in db.execute("SELECT flow, status FROM reaction"))
