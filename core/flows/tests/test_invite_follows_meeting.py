"""The bot follows the meeting (ADR-0050, founder 2026-10-14 collaboration target step 3): an invite
to the shared mailbox arms ONE keyed schedule per meeting (UID), an update moves it, a cancellation
removes it, and it fires one minute before the start into `invite_join` — the steps that used to run
after `await_start`. The invite's own intake (account, RSVP, ack, prep) still runs on every invite."""
from __future__ import annotations

import calendar
import json

from flows import FakeClock, Registry, keyed, tick
from flows_defs import production
from flows_integrations.mailbox import handle, route
from mail_auth_fixture import authed
from sqlite_double import SqliteDB

START = calendar.timegm((2030, 8, 23, 15, 30, 0))
BASE = """BEGIN:VCALENDAR\nMETHOD:{method}\nBEGIN:VEVENT\nDTSTART:{start}\nDTEND:20300823T170000Z
ORGANIZER;CN=Org:mailto:org@vexa.test\nUID:meet-0001@google.com\nSEQUENCE:{seq}
LOCATION:https://meet.google.com/abc-defg-hij\nSUMMARY:Sync\nEND:VEVENT\nEND:VCALENDAR"""


def _ics(method="REQUEST", start="20300823T153000Z", seq=0):
    return BASE.format(method=method, start=start, seq=seq)


class _Msg:
    def __init__(self, ics, mid):
        self.frm, self.headers, self.ics, self.message_id, self.ext_id = "org@vexa.test", authed("org@vexa.test"), ics, mid, mid


def _send(db, reg, clock, ics, mid):
    return handle(db, reg, clock, "info@vexa.test", _Msg(ics, mid), known_uid=lambda e: "7",
                  is_scaffolded=lambda u: True, provision=None, rules={"mail_domains": ("vexa.test",)})


def _rig():
    db, clock, reg = SqliteDB(), FakeClock(START - 86400), Registry()
    production.build(reg, db)
    assert production.seed(db) == ["invite_join", "meeting_signal", "room_signal"]
    assert production.seed(db) == [], "a seed is written once, never overwritten"
    reg.refresh_from_db(db)
    return db, clock, reg


def _pending(db):
    return [(k, at) for k, at, st in db.execute("SELECT key, fire_at, status FROM keyed_schedule")
            if st == "armed"]


def test_an_invite_arms_one_bot_a_minute_before_and_an_update_moves_it():
    db, clock, reg = _rig()
    assert _send(db, reg, clock, _ics(), "m1")[0] == "invite"
    assert _pending(db) == [("meet-0001@google.com", START - 60)]
    intake = db.execute("SELECT flow FROM reaction WHERE event_type = 'invite.received'")
    assert [r[0] for r in intake] == ["invite_intake"], "the intake runs; the bot waits on the schedule"
    _send(db, reg, clock, _ics(start="20300823T163000Z", seq=1), "m2")
    assert _pending(db) == [("meet-0001@google.com", START + 3600 - 60)], "moved, not doubled"


def test_a_cancellation_removes_the_bot():
    db, clock, reg = _rig()
    _send(db, reg, clock, _ics(), "m1")
    assert _send(db, reg, clock, _ics(method="CANCEL", seq=2), "m3")[0] == "invite_cancel"
    assert _pending(db) == []


def test_it_fires_once_into_invite_join_with_the_invites_refs():
    db, clock, reg = _rig()
    _send(db, reg, clock, _ics(), "m1")
    clock.advance(86400 - 60 + 1)
    assert keyed.fire_due(db, reg, clock) == 1
    assert keyed.fire_due(db, reg, clock) == 0
    flow, refs = db.execute("SELECT flow, subject_refs FROM reaction WHERE event_type = 'keyed.due'")[0]
    refs = json.loads(refs)
    assert flow == "invite_join" and refs["url"] == "https://meet.google.com/abc-defg-hij"
    assert refs["organizer"] == "org@vexa.test" and refs["event_key"] == "meet-0001@google.com"


def test_a_cancel_from_an_organizer_we_would_not_act_for_is_ignored():
    assert route(None, "info@vexa.test", "x@evil.test", authed("x@evil.test"), _ics(method="CANCEL").replace(
        "org@vexa.test", "x@evil.test"), lambda e: None, lambda u: False, allowed={"vexa.test"}) is None


def test_the_bot_carries_the_invites_workspace_and_falls_back_when_it_is_refused(monkeypatch):
    from flows import Done, StepCtx
    from flows_steps import meeting
    calls = []

    def http(method, url, headers, body=None, timeout=20):
        assert headers.get("X-User-Id") == "7" and headers.get("X-Vexa-Service-Key"), headers
        assert "X-API-Key" not in headers
        calls.append(dict(body or {}))
        if body and body.get("workspace_id") == "ws-nope":
            return 403, {"detail": "not a member"}
        return 201, {"id": 9, "native_meeting_id": "abc-defg-hij", "platform": "google_meet"}
    monkeypatch.setattr(meeting, "http", http)

    class R:
        reaction_id = "r"
    for ws, want in (("ws-1", ["ws-1"]), ("ws-nope", ["ws-nope", None]), ("", [None])):
        calls.clear()
        ctx = StepCtx(reaction=R(), effect_key="e", prior={"ensure_user": {"uid": "7"}}, clock_now=0,
                      scratch={})
        ctx.reaction.subject_refs = {"url": "https://meet.google.com/abc-defg-hij", "workspace": ws}
        out = meeting.dispatch_bot(ctx)
        assert isinstance(out, Done) and [c.get("workspace_id") for c in calls] == want


def test_an_invite_that_arrives_after_the_meeting_started_still_sends_the_bot():
    """Invite the bot to a meeting already running (forwarded mid-meeting): the fire time is in the
    past when the row is armed, so lateness counts from when the row was armed, not from the slot.
    A row the worker could have fired on time and did not (downtime) is still `missed`."""
    db, clock, reg = _rig()
    clock.advance(86400 + 20 * 60)                     # 20 minutes into the meeting
    _send(db, reg, clock, _ics(), "late")
    assert keyed.fire_due(db, reg, clock) == 1, "a late invite still gets its bot"
    assert keyed.fire_due(db, reg, clock) == 0, "and only once"


def test_a_row_armed_in_time_and_left_unfired_past_the_catch_up_is_missed():
    db, clock, reg = _rig()
    _send(db, reg, clock, _ics(), "m1")
    clock.advance(86400 - 60 + keyed.CATCHUP_S + 30)   # the worker was down past the catch-up
    assert keyed.fire_due(db, reg, clock) == 0
    assert [s for (s,) in db.execute("SELECT status FROM keyed_schedule")] == ["missed"]
