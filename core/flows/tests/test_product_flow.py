"""The founder-spec product flow, end to end at fixture level:
new person invites vexa@bank.com → organizer notified + bot scheduled → onboarding sub-flow
(research → one question → human replies) → meeting completes BEFORE onboarding finishes →
processing QUEUES with follow-up nudges → human finishes setup → summary flows out."""
from __future__ import annotations

import flows as F
from flows import EventType, admit, resume, status, tick
from sqlite_double import SqliteDB
from flows_defs.onboard_and_meet import register
from flows_steps.fakes import FakeWorld, build_registry
from fixtures import drain
from loopback import LoopbackWorld, drain_with_world


def product_rig():
    world = LoopbackWorld(duration_s=1800.0, webhook_redeliveries=1)
    reg = build_registry(world)
    db = SqliteDB()
    clock = F.FakeClock()
    world.admit_fn = lambda **kw: admit(db, reg, clock, **kw)   # fact-emitting steps loop back
    register(reg)

    # bot dispatch tells the loopback world so the meeting later completes via "webhook"
    inner = reg.steps["dispatch_bot"]
    def dispatch_bot(ctx):
        out = inner(ctx)
        world.on_dispatch(ctx.refs["meeting"], ctx.refs, ctx.clock_now)
        return out
    reg.steps["dispatch_bot"] = dispatch_bot
    return db, reg, clock, world


REFS = {"meeting": "m-first", "inviter": "marvin@bank.com",
        "participants": ["marvin@bank.com", "lena@bank.com", "out@other.io"],
        "start_time": 1_000_000.0 + 3600}


def _pump(db, reg, clock, world, seconds, step=300.0):
    """Advance wall time in slices, letting reactions AND the world move — no jumps past nudges."""
    end = clock.now() + seconds
    while clock.now() < end:
        world.pump(db, reg, clock)
        F.reclaim(db, clock); F.escalate(db, clock)
        if not tick(db, reg, clock):
            clock.advance(step)


def test_first_meeting_queues_until_onboarding_finishes():
    db, reg, clock, world = product_rig()

    admit(db, reg, clock, source_event_id="inv-first", event_type="invite.received", subject_refs=REFS)
    _pump(db, reg, clock, world, 300)

    # organizer notified + bot scheduled + onboarding sub-flow spawned and ASKING
    assert ("marvin@bank.com", "scheduled") in world.emails
    assert world.research == ["marvin@bank.com"]
    assert ("marvin@bank.com", "onboarding-question") in world.emails
    onb = {f: st for f, st in db.execute("SELECT flow, status FROM reaction WHERE flow='onboard_by_email'")}
    assert onb == {"onboard_by_email": "blocked"}          # waiting on the human

    # meeting happens on time (bot at start−2min), completes, webhook returns
    _pump(db, reg, clock, world, 3 * 3600)
    assert world.bots_dispatched == ["m-first"]
    # …but the human never replied: processing is QUEUED, nudging — never lost, never processed
    gated = db.execute("SELECT status FROM reaction WHERE flow='post_meeting_gated'")[0][0]
    assert gated == "retrying"
    assert world.commits == []                             # NO summary before the workspace exists
    assert world.followups.count("marvin@bank.com") >= 2   # follow-ups went out on cadence

    # the human finally answers the onboarding question → workspace completes → queue drains
    rid = db.execute("SELECT reaction_id FROM reaction WHERE flow='onboard_by_email'")[0][0]
    resume(db, rid, actor="marvin@bank.com", clock=clock, reason="yes — Marvin, treasury lead")
    _pump(db, reg, clock, world, 2 * 3600)

    assert "marvin@bank.com" in world.workspaces_ready
    assert world.commits == ["sha-m-first"]                # the FIRST meeting got processed after all
    assert ("marvin@bank.com", "sha-m-first") in world.emails
    assert ("lena@bank.com", "sha-m-first") in world.emails
    assert not any(r == "out@other.io" for r, _ in world.emails)
    ends = {f: st for f, st in db.execute("SELECT flow, status FROM reaction")}
    assert ends == {"invite_intake": "done", "onboard_by_email": "done", "post_meeting_gated": "done"}


def test_known_person_skips_onboarding_entirely():
    db, reg, clock, world = product_rig()
    world.workspaces_ready.add("marvin@bank.com")          # returning user
    admit(db, reg, clock, source_event_id="inv-2", event_type="invite.received", subject_refs=REFS)
    _pump(db, reg, clock, world, 4 * 3600)
    assert db.execute("SELECT COUNT(*) FROM reaction WHERE flow='onboard_by_email'")[0][0] == 0
    assert world.followups == []                           # no nudges — straight through
    assert world.commits == ["sha-m-first"]


def test_duplicate_invite_and_duplicate_webhook_still_one_of_everything():
    db, reg, clock, world = product_rig()
    world.workspaces_ready.add("marvin@bank.com")
    for _ in range(3):
        admit(db, reg, clock, source_event_id="inv-3", event_type="invite.received", subject_refs=REFS)
    _pump(db, reg, clock, world, 4 * 3600)
    assert world.bots_dispatched == ["m-first"]
    assert world.commits == ["sha-m-first"]
    assert world.emails.count(("marvin@bank.com", "sha-m-first")) == 1
