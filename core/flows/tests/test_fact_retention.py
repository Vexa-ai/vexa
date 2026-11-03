"""Captured facts expire (PRD 0.13.4: a Fact is admitted, then expires; SCOPE TRU-7: captured fact
bodies and mail bodies expire, the period documented).

A mail reply is admitted with its body in `reaction.subject_refs.text`; a refused mail leaves its
sender and the refused invite's meeting facts in `mail_quarantine`. Nothing removed either: the
security validation of 2026-10-17 (M5) and every gate since found no retention on reactions,
receipts or the quarantine. These tests drive the WORKER's own pass (`retention_pass`, the one the
loop runs) over rows the real writers made, and pin both halves: what is past the period goes, and
what is inside it — or still live — stays."""
from __future__ import annotations

import inspect
import json

from fixtures import INVITE_REFS, drain, rig
from flows import FakeClock, admit, retry, status
from flows_integrations import mail_policy
from sqlite_double import SqliteDB

import flows_worker.__main__ as worker

DAY = 86_400.0
BODY = "Please move the board meeting, my diagnosis came back, keep this between us."


def _count(db, table: str) -> int:
    return int(db.execute(f"SELECT COUNT(*) FROM {table}")[0][0])


def _mail_reply(db, clock, sid: str, status_: str) -> None:
    """A mail.reply reaction exactly as the mailbox admits one (`mailbox.handle`), at `status_`."""
    db.execute(
        """INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs, flow,
                                 flow_version, step, status, attempt, next_run_at, reason, scratch,
                                 created_at, updated_at)
           VALUES (:rid, :sid, 'mail.reply', :refs, 'mail_reply', 1, 'reply', :st, 0, :now, NULL,
                   :scratch, :now, :now)""",
        {"rid": f"r-{sid}", "sid": sid, "st": status_, "now": clock.now(),
         "refs": json.dumps({"uid": "7", "from_addr": "ann@bank.test", "text": BODY,
                             "subject": "Re: board"}),
         "scratch": json.dumps({"draft": BODY})})
    db.execute("""INSERT INTO effect_receipt (effect_key, reaction_id, step, state, result,
                                             attempted_at, confirmed_at)
                  VALUES (:k, :rid, 'reply', 'confirmed', :res, :now, :now)""",
               {"k": f"r-{sid}:reply", "rid": f"r-{sid}", "res": json.dumps({"sent": BODY}),
                "now": clock.now()})


def _bodies(db) -> list:
    rows = db.execute("SELECT subject_refs, scratch FROM reaction")
    rows += db.execute("SELECT result, '' FROM effect_receipt")
    return [r for r in rows if BODY in (r[0] or "") + (r[1] or "")]


def test_a_finished_mail_reply_and_its_receipts_expire_after_the_period(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_FACT_RETENTION_DAYS", raising=False)   # the default: 90 days
    db, reg, clock, world = rig()
    _mail_reply(db, clock, "mail-old", "done")
    clock.advance(91 * DAY)
    assert _bodies(db), "the precondition: the body is stored on the reaction and its receipt"
    gone = worker.retention_pass(db, clock)
    assert gone["reactions"] == 1 and gone["receipts"] == 1
    assert _bodies(db) == []
    assert _count(db, "reaction") == 0 and _count(db, "effect_receipt") == 0


def test_a_real_run_with_signals_expires_whole():
    """A reaction the engine itself drove to done — receipts per step, an operator signal — goes
    with every row that names it (the foreign keys would refuse a reaction-only delete)."""
    db, reg, clock, world = rig()
    admit(db, reg, clock, source_event_id="ev-1", event_type="invite.received",
          subject_refs=INVITE_REFS)
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    drain(db, reg, clock)
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    assert status(db, rid)["status"] == "done"
    db.execute("INSERT INTO signal (signal_id, reaction_id, kind, actor, reason, created_at) "
               "VALUES ('s1', :r, 'wake', 'op', 'x', :now)", {"r": rid, "now": clock.now()})
    assert _count(db, "effect_receipt") > 1
    clock.advance(90 * DAY + 1)
    gone = worker.retention_pass(db, clock)
    assert gone["signals"] == 1 and gone["receipts"] > 1 and gone["reactions"] == 1
    assert _count(db, "reaction") == _count(db, "effect_receipt") == _count(db, "signal") == 0


def test_quarantined_mail_and_old_mail_turns_expire():
    db, clock = SqliteDB(), FakeClock(1_000_000.0)
    mail_policy.quarantine(db, ext_id="q-old", frm="stranger@evil.test", kind="unverified_invite",
                           reason="organizer unknown", facts={"title": "Board", "participants":
                                                              ["ann@bank.test"]}, at=clock.now())
    mail_policy.record_turn(db, "t-old", "ann@bank.test", clock.now())
    clock.advance(91 * DAY)
    mail_policy.quarantine(db, ext_id="q-new", frm="other@evil.test", kind="stranger_mail",
                           reason="not in the allow-list", facts={"subject": "hi"}, at=clock.now())
    mail_policy.record_turn(db, "t-new", "ann@bank.test", clock.now())
    gone = worker.retention_pass(db, clock)
    assert gone["quarantine"] == 1 and gone["mail_turns"] == 1
    # POSITIVE CONTROL: the rows inside the period stay, and the rate window still counts them
    assert [r[0] for r in db.execute("SELECT ext_id FROM mail_quarantine")] == ["q-new"]
    assert [r[0] for r in db.execute("SELECT ext_id FROM mail_turn")] == ["t-new"]


def test_inside_the_period_and_still_live_reactions_stay():
    """POSITIVE CONTROLS. A finished reaction younger than the period stays; a reaction that is
    still waiting (admitted, retrying, running, blocked) stays however old — its refs are what it
    runs on."""
    db, reg, clock, world = rig()
    for st in ("admitted", "retrying", "running", "blocked"):
        _mail_reply(db, clock, f"live-{st}", st)
    clock.advance(200 * DAY)
    _mail_reply(db, clock, "young-done", "done")
    clock.advance(89 * DAY)
    gone = worker.retention_pass(db, clock)
    assert gone["reactions"] == 0
    assert sorted(r[0] for r in db.execute("SELECT source_event_id FROM reaction")) == [
        "live-admitted", "live-blocked", "live-retrying", "live-running", "young-done"]
    assert _count(db, "effect_receipt") == 5


def test_a_failed_reaction_retried_is_live_again_and_stays():
    db, reg, clock, world = rig()
    _mail_reply(db, clock, "f-1", "failed")
    clock.advance(100 * DAY)
    assert retry(db, "r-f-1", "operator", clock)
    assert worker.retention_pass(db, clock)["reactions"] == 0
    assert _count(db, "reaction") == 1


def test_zero_keeps_everything(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_FACT_RETENTION_DAYS", "0")
    db, reg, clock, world = rig()
    _mail_reply(db, clock, "mail-old", "done")
    mail_policy.quarantine(db, ext_id="q", frm="s@evil.test", kind="stranger_mail", reason="x",
                           at=clock.now())
    clock.advance(3650 * DAY)
    assert not any(v for v in worker.retention_pass(db, clock).values())
    assert _count(db, "reaction") == 1 and _count(db, "mail_quarantine") == 1


def test_the_period_is_the_configured_one(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_FACT_RETENTION_DAYS", "30")
    db, reg, clock, world = rig()
    _mail_reply(db, clock, "a", "done")
    clock.advance(29 * DAY)
    assert worker.retention_pass(db, clock)["reactions"] == 0
    clock.advance(2 * DAY)
    assert worker.retention_pass(db, clock)["reactions"] == 1


def test_a_spent_keyed_schedule_loses_its_payload_and_keeps_its_row():
    """The row is what stops a redelivered calendar event from arming the same fire again
    (`keyed.upsert`), so it stays; the event's facts on it expire."""
    db, reg, clock, world = rig()
    for key, st in (("ev-fired", "fired"), ("ev-armed", "armed")):
        db.execute("INSERT INTO keyed_schedule (scope, flow, key, fire_at, status, version, "
                   "event_type, person, payload, fired_for, updated_at) VALUES ('instance', "
                   "'invite_join', :k, :at, :st, 1, 'calendar.event.created', '7', :p, NULL, :now)",
                   {"k": key, "at": clock.now(), "st": st, "now": clock.now(),
                    "p": json.dumps({"title": "Board", "attendees": ["ann@bank.test"]})})
    clock.advance(91 * DAY)
    assert worker.retention_pass(db, clock)["keyed_payloads"] == 1
    rows = dict(db.execute("SELECT key, payload FROM keyed_schedule"))
    assert rows["ev-fired"] is None
    assert json.loads(rows["ev-armed"])["title"] == "Board", "an armed fire keeps what it runs on"


def test_a_backlog_drains_in_batches():
    from flows import retention
    db, reg, clock, world = rig()
    for i in range(7):
        _mail_reply(db, clock, f"b{i}", "done")
    clock.advance(91 * DAY)
    first = retention.purge(db, clock, days=90, batch=5)
    assert first["reactions"] == 5 and first["more"] is True
    second = retention.purge(db, clock, days=90, batch=5)
    assert second["reactions"] == 2 and second["more"] is False


def test_the_worker_loop_runs_the_retention_pass():
    """A purge nothing calls is not retention: the pass is in the worker's reconciliation tick,
    guarded like every other pass."""
    body = inspect.getsource(worker.main)
    assert 'guarded("retention", retention_pass, db, clock)' in body


def test_the_period_is_declared_with_its_default():
    import flows_config
    cls, default, why = flows_config.DECLARED["VEXA_FLOWS_FACT_RETENTION_DAYS"]
    assert (cls, default) == ("defaulted", "90")
    assert "0 keeps everything" in why


def test_the_default_is_ninety_days(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_FACT_RETENTION_DAYS", raising=False)
    db, reg, clock, world = rig()
    _mail_reply(db, clock, "x", "done")
    clock.advance(90 * DAY - 1)
    assert worker.retention_pass(db, clock)["reactions"] == 0
    clock.advance(2)
    assert worker.retention_pass(db, clock)["reactions"] == 1
