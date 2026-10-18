"""Access vectors for a person's flows (ADR-0046): budgets, submission caps, and rows that a tighter
policy no longer allows. Offline: the engine on the sqlite double, the clock faked."""
from __future__ import annotations

import calendar
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows import Done, FakeClock, Registry, admission, admit, schedule  # noqa: E402
from flows.registry import SCHEDULE_EVENT  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

T = float(calendar.timegm((2027, 3, 1, 6, 59, 30)))


def _row(db, *, scope="user:42", name="brief", version=1, cron="0 7 * * *", event=None,
         steps=("note",), params=None, status="active"):
    db.execute(
        "INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, status, "
        "created_by, created_at) VALUES (:sc,:n,:v,:e,:c,:s,:p,:st,'t',0)",
        {"sc": scope, "n": name, "v": version, "e": event or SCHEDULE_EVENT,
         "c": None if event else cron, "s": json.dumps(list(steps)),
         "p": json.dumps(params or {}), "st": status})


def _rig():
    db, clock, reg = SqliteDB(), FakeClock(T), Registry()

    @reg.step
    def note(ctx):
        return Done({})

    @reg.step
    def wide(ctx):
        return Done({})

    return db, clock, reg


# ── F4: events about a person do not spend that person's schedule budget ─────────────────────────

def test_message_triggered_runs_do_not_silence_scheduled_routines():
    db, clock, reg = _rig()
    _row(db, name="on-msg", event="thread.message")
    _row(db, name="brief", cron="0 7 * * *")
    reg.refresh_from_db(db)
    # another person sends enough messages to use up the event budget
    made = [admit(db, reg, clock, source_event_id=f"msg:9:{i}", event_type="thread.message",
                  subject_refs={"uid": "42", "sender_uid": "9"}, runs_per_day=3,
                  messages_per_day=-1) for i in range(5)]
    assert made == [1, 1, 1, 0, 0]
    # the scheduled routine still fires at 07:00
    clock.advance(60)
    assert schedule.fire_due(db, reg, clock, runs_per_day=3) == 1


def test_schedule_fires_keep_their_own_cap():
    db, clock, reg = _rig()
    _row(db, name="brief", cron="0 7 * * *")
    reg.refresh_from_db(db)
    flow = next(f for f in reg.flows.values() if f.owner)
    refs = {"uid": "42", "slot": 1.0}
    for i in range(2):
        assert admission.admissible(db, flow, clock, event_type=SCHEDULE_EVENT,
                                    subject_refs=refs, runs_per_day=2)
        admission.insert(db, clock, flow, key=f"k{i}", event_type=SCHEDULE_EVENT, subject_refs=refs)
    assert not admission.admissible(db, flow, clock, event_type=SCHEDULE_EVENT,
                                    subject_refs=refs, runs_per_day=2)
    # …and the event bucket is untouched by those fires
    _row(db, name="on-thing", event="thing.happened")
    reg.refresh_from_db(db)
    assert admit(db, reg, clock, source_event_id="e1", event_type="thing.happened",
                 subject_refs={"uid": "42"}, runs_per_day=2) == 1


# ── F6: a row a tighter policy no longer allows stops being chosen ───────────────────────────────

def test_a_user_row_the_policy_no_longer_allows_is_not_loaded():
    db, clock, reg = _rig()
    _row(db, name="wide", steps=("wide",), event="thing.happened")
    _row(db, name="ok", steps=("note",), event="thing.happened")
    reg.row_check = lambda scope, name, steps, cron: (
        "step_not_allowed" if any(s != "note" for s in steps) else None)
    reg.refresh_from_db(db)
    loaded = {f.name for f in reg.flows.values() if f.owner}
    assert loaded == {"ok"}


def test_a_loaded_user_row_is_dropped_when_the_policy_tightens():
    db, clock, reg = _rig()
    _row(db, name="wide", steps=("wide",), event="thing.happened")
    reg.refresh_from_db(db)
    assert admit(db, reg, clock, source_event_id="e1", event_type="thing.happened",
                 subject_refs={"uid": "42"}) == 1
    reg.row_check = lambda scope, name, steps, cron: "step_not_allowed"
    reg.refresh_from_db(db)
    assert admit(db, reg, clock, source_event_id="e2", event_type="thing.happened",
                 subject_refs={"uid": "42"}) == 0


def test_instance_rows_are_not_held_to_the_person_policy():
    db, clock, reg = _rig()
    _row(db, scope="instance", name="wide", steps=("wide",), event="thing.happened")
    reg.row_check = lambda scope, name, steps, cron: "step_not_allowed"
    reg.refresh_from_db(db)
    assert any(f.name == "wide" and not f.owner for f in reg.flows.values())


def test_the_composed_row_check_holds_the_allow_list_and_the_minimum_interval():
    from flows_defs import production
    from flows_steps import policies

    db, clock, reg = _rig()
    reg.user_allowed = {"note"}
    check = production.user_row_check(reg, read=lambda uid: policies.resolve(None), floor=900)
    assert check("user:42", "ok", ["note"], "0 7 * * *") is None
    assert check("user:42", "wide", ["wide"], None) == "step_not_allowed"
    assert check("user:42", "often", ["note"], "* * * * *") == "quota_min_interval"
    # a policy that narrows the list refuses a row it no longer names
    narrowed = production.user_row_check(
        reg, read=lambda uid: policies.resolve("---\nuser_flow_steps: wide\n---\n"), floor=900)
    assert narrowed("user:42", "ok", ["note"], None) == "step_not_allowed"
