"""ADR-0052 §7 — the keyed schedule a guarantee is built on (slice s2a, Vexa-ai/vexa#1962).

* a row with a deadline is missed only past it, so a worker down from the fire time almost to the
  deadline still fires when it returns;
* an unchanged re-upsert (a duplicate delivery) does not extend the window a row may fire in;
* rows `fire_due` skips (refused by the daily cap) never stop a later due row, and rows of a retired
  flow are marked `retired` instead of sitting in front of everyone else's;
* the shares count per flow, and a code flow with `share: account` has no company total;
* a full share is answered to the caller, not only logged;
* `invite_join` is registered in code; its stored seed copy is removed once."""
from __future__ import annotations

import calendar
import json
import logging

from flows import FakeClock, Registry, admit, keyed, triggers
from flows.registry import EventType
from flows_defs import production
from sqlite_double import SqliteDB

T = float(calendar.timegm((2030, 3, 1, 9, 0, 0)))          # the meeting's start
LEAD, GRACE = 600, 600
TRIGGER = {"kind": "relative", "at": "payload.start - 600s", "deadline": "payload.start + 600s",
           "key": "payload.meeting_id"}


def _rig(trigger=TRIGGER, name="booked", event="planned.changed", extra=()):
    db, clock, reg = SqliteDB(), FakeClock(T - 3600), Registry()

    @reg.step
    def note(ctx):  # noqa: ARG001 — the fire is what is under test, not the step
        from flows import Done
        return Done({})
    reg.flow(name=name, version=1, on=EventType(event), steps=[note], params={"_trigger": trigger})
    for n, t, e in extra:
        reg.flow(name=n, version=1, on=EventType(e), steps=[note], params={"_trigger": t})
    return db, clock, reg


def _book(db, reg, clock, mid, start=T, uid="7", event="planned.changed", refusals=None):
    return admit(db, reg, clock, source_event_id=f"{mid}-{clock.now()}", event_type=event,
                 subject_refs={"meeting_id": mid, "start": start, "uid": uid}, refusals=refusals)


def _status(db, key):
    return db.execute("SELECT status FROM keyed_schedule WHERE key = :k", {"k": key})[0][0]


# ── missed is judged from the deadline ───────────────────────────────────────────────────────────

def test_a_worker_down_from_the_fire_time_almost_to_the_deadline_still_fires():
    db, clock, reg = _rig()
    _book(db, reg, clock, "m1")
    clock._t = T - LEAD                                      # fire time: the worker is down …
    clock._t = T + GRACE - 1                                 # … for 19 minutes, past CATCHUP_S
    assert T + GRACE - 1 - (T - LEAD) > keyed.CATCHUP_S
    assert keyed.fire_due(db, reg, clock) == 1, "the meeting has not passed its deadline: it fires"
    assert _status(db, "m1") == "fired"


def test_past_the_deadline_the_row_is_missed():
    db, clock, reg = _rig()
    _book(db, reg, clock, "m1")
    clock._t = T + GRACE + 1
    assert keyed.fire_due(db, reg, clock) == 0
    assert _status(db, "m1") == "missed"


def test_a_row_without_a_deadline_keeps_the_catch_up_rule():
    db, clock, reg = _rig(trigger={k: v for k, v in TRIGGER.items() if k != "deadline"})
    _book(db, reg, clock, "m1")
    clock._t = T - LEAD + keyed.CATCHUP_S + 1
    assert keyed.fire_due(db, reg, clock) == 0
    assert _status(db, "m1") == "missed"


def test_the_trigger_carries_the_deadline_and_never_the_share_from_a_submission():
    spec = triggers.check_spec({**TRIGGER, "share": "account"})
    assert spec["deadline"] == "payload.start + 600s" and "share" not in spec


# ── an unchanged re-upsert does not extend the window ────────────────────────────────────────────

def test_an_unchanged_re_upsert_does_not_extend_the_window():
    db, clock, reg = _rig(trigger={k: v for k, v in TRIGGER.items() if k != "deadline"})
    _book(db, reg, clock, "m1")
    armed_at = db.execute("SELECT updated_at FROM keyed_schedule")[0][0]
    clock._t = T - LEAD + keyed.CATCHUP_S - 5                # the worker is down; a duplicate arrives
    _book(db, reg, clock, "m1")
    assert db.execute("SELECT updated_at FROM keyed_schedule")[0][0] == armed_at
    clock._t = T - LEAD + keyed.CATCHUP_S + 1
    assert keyed.fire_due(db, reg, clock) == 0, "the duplicate did not buy it ten more minutes"
    assert _status(db, "m1") == "missed"


def test_a_same_time_redelivery_does_not_re_arm_a_missed_row():
    db, clock, reg = _rig(trigger={k: v for k, v in TRIGGER.items() if k != "deadline"})
    _book(db, reg, clock, "m1")
    clock._t = T - LEAD + keyed.CATCHUP_S + 1
    keyed.fire_due(db, reg, clock)
    assert _status(db, "m1") == "missed"
    _book(db, reg, clock, "m1")                              # the same meeting, the same time, again
    assert _status(db, "m1") == "missed"
    assert keyed.fire_due(db, reg, clock) == 0, "a redelivery never fires a missed row late"
    _book(db, reg, clock, "m1", start=T + 3600)               # a real move re-arms it
    assert _status(db, "m1") == "armed"


def test_a_real_move_still_re_arms():
    db, clock, reg = _rig()
    _book(db, reg, clock, "m1")
    _book(db, reg, clock, "m1", start=T + 3600)
    at, dl = db.execute("SELECT fire_at, deadline FROM keyed_schedule")[0]
    assert (at, dl) == (T + 3600 - LEAD, T + 3600 + GRACE)


# ── no head-of-line block ────────────────────────────────────────────────────────────────────────

def test_201_rows_the_cap_refuses_do_not_stop_a_later_due_fire():
    db, clock, reg = _rig(extra=[("other", {**TRIGGER}, "other.changed")])
    for i in range(201):
        _book(db, reg, clock, f"a{i:03d}", start=T - 60)      # due first, all refused below
    _book(db, reg, clock, "z", start=T, uid="8", event="other.changed")
    clock._t = T - LEAD + 30
    refused = lambda flow, refs: flow.name != "booked"         # noqa: E731
    assert keyed.fire_due(db, reg, clock, admissible=refused) == 1
    assert _status(db, "z") == "fired"
    assert {s for (s,) in db.execute("SELECT status FROM keyed_schedule WHERE flow = 'booked'")} == {"armed"}


def test_201_rows_of_a_retired_flow_are_marked_retired_and_do_not_stop_a_due_fire():
    db, clock, reg = _rig()
    db.execute("INSERT INTO flow_version (scope, name, version, on_event, steps, params, status, "
               "created_by, created_at) VALUES ('instance', 'gone', 1, 'gone.changed', '[\"note\"]', "
               ":p, 'retired', 't', 0)", {"p": json.dumps({"_trigger": TRIGGER})})
    for i in range(201):
        db.execute("INSERT INTO keyed_schedule (scope, flow, key, fire_at, status, version, event_type, "
                   "person, payload, fired_for, updated_at, deadline) VALUES ('instance', 'gone', :k, "
                   ":at, 'armed', 1, 'gone.changed', '7', '{}', NULL, :at, NULL)",
                   {"k": f"g{i:03d}", "at": T - LEAD - 60})
    _book(db, reg, clock, "m1")
    clock._t = T - LEAD + 1
    assert keyed.fire_due(db, reg, clock) == 1
    assert _status(db, "m1") == "fired"
    assert db.execute("SELECT status, COUNT(*) FROM keyed_schedule WHERE flow = 'gone' "
                      "GROUP BY status") == [("retired", 201)]


def test_a_flow_active_in_the_table_but_not_loaded_keeps_its_rows_armed():
    db, clock, reg = _rig()
    db.execute("INSERT INTO flow_version (scope, name, version, on_event, steps, params, status, "
               "created_by, created_at) VALUES ('user:9', 'mine', 1, 'x.y', '[\"no_such_step\"]', "
               ":p, 'active', 't', 0)", {"p": json.dumps({"_trigger": TRIGGER})})
    db.execute("INSERT INTO keyed_schedule (scope, flow, key, fire_at, status, version, event_type, "
               "person, payload, fired_for, updated_at, deadline) VALUES ('user:9', 'mine', 'k', :at, "
               "'armed', 1, 'x.y', '9', '{}', NULL, :at, NULL)", {"at": T - LEAD})
    clock._t = T - LEAD + 1
    keyed.fire_due(db, reg, clock)
    assert _status(db, "k") == "armed", "a policy refusal or a missing step is not a retirement"


def test_the_pass_pages_through_more_than_one_page():
    db, clock, reg = _rig()
    for i in range(450):
        _book(db, reg, clock, f"m{i:03d}", uid=str(i))
    clock._t = T - LEAD + 1
    assert keyed.fire_due(db, reg, clock, page=200) == 450


# ── shares ───────────────────────────────────────────────────────────────────────────────────────

def test_rows_of_one_flow_do_not_use_another_flows_share():
    db, clock, reg = _rig(extra=[("other", {**TRIGGER}, "other.changed")])
    for i in range(3):
        _book(db, reg, clock, f"o{i}", event="other.changed")
    flow = reg.newest("instance", "booked")
    assert keyed.upsert(db, clock, flow, key="b0", fire_at=T, event_type="x", payload={"uid": "7"},
                        per_user=3) == "armed"
    assert keyed.held(db, "instance", "booked", "7") == 1
    assert keyed.held(db, "instance", "other", "7") == 3


def _fill(db, flow_name, people, per_person):
    for p in range(people):
        for i in range(per_person):
            db.execute("INSERT INTO keyed_schedule (scope, flow, key, fire_at, status, version, "
                       "event_type, person, payload, fired_for, updated_at, deadline) VALUES "
                       "('instance', :f, :k, :at, 'armed', 1, 'x', :p, NULL, NULL, :at, NULL)",
                       {"f": flow_name, "k": f"{p}-{i}", "at": T, "p": f"acct-{p}"})


def test_41_accounts_at_their_share_do_not_refuse_a_42nd_on_an_account_share_flow():
    db, clock, reg = _rig(trigger={**TRIGGER, "share": "account"})
    _fill(db, "booked", 41, keyed.PER_USER)                    # 20,500 rows: over the company total
    assert keyed.held(db, "instance", "booked") > keyed.COMPANY_MAX
    refusals: list = []
    _book(db, reg, clock, "new", uid="acct-41", refusals=refusals)
    assert _status(db, "new") == "armed" and refusals == []


def test_a_company_flow_without_the_account_share_still_has_its_total():
    db, clock, reg = _rig()
    _fill(db, "booked", 41, keyed.PER_USER)
    refusals: list = []
    _book(db, reg, clock, "new", uid="acct-41", refusals=refusals)
    assert db.execute("SELECT COUNT(*) FROM keyed_schedule WHERE key = 'new'")[0][0] == 0
    assert refusals == ["keyed_share_full"], "a full share is answered to the caller"


def test_a_full_personal_share_is_answered_to_the_caller():
    db, clock, reg = _rig(trigger={**TRIGGER, "share": "account"})
    _fill(db, "booked", 1, keyed.PER_USER)
    refusals: list = []
    _book(db, reg, clock, "one-too-many", uid="acct-0", refusals=refusals)
    assert refusals == ["keyed_share_full"]


# ── invite_join: code, not a seed ────────────────────────────────────────────────────────────────

def _seed_row(db, version=1, status="active", by="seed"):
    db.execute("INSERT INTO flow_version (scope, name, version, on_event, steps, params, status, "
               "created_by, created_at) VALUES ('instance', 'invite_join', :v, 'invite.*', "
               "'[\"ensure_user\"]', '{}', :st, :by, 0)", {"v": version, "st": status, "by": by})


def test_invite_join_is_registered_in_code_and_no_longer_seeded():
    reg, db = Registry(), SqliteDB()
    production.build(reg, db)
    join = reg.newest("instance", "invite_join")
    assert join is not None and ("invite_join", 1) not in reg.db_versions
    assert triggers.spec_of(join)["key"] == "payload.event_key"
    assert "invite_join" not in [d["name"] for d in production.SEEDS]
    assert "invite_join" not in production.seed(db)


def test_the_stored_seed_copy_is_removed_once():
    db = SqliteDB()
    _seed_row(db)
    assert production.drop_former_seeds(db) == ["invite_join"]
    assert db.execute("SELECT COUNT(*) FROM flow_version WHERE name = 'invite_join'")[0][0] == 0
    assert production.drop_former_seeds(db) == []


def test_the_companys_own_versions_are_retired_never_deleted(caplog):
    db = SqliteDB()
    _seed_row(db, status="retired")
    _seed_row(db, version=2, by="operator")
    with caplog.at_level(logging.WARNING, logger="flows.production"):
        assert production.drop_former_seeds(db) == ["invite_join"]
    assert db.execute("SELECT version, status, created_by FROM flow_version "
                      "WHERE name = 'invite_join'") == [(2, "retired", "operator")]
    assert "v2 (active, by operator)" in " ".join(r.getMessage() for r in caplog.records)


def test_a_version_submitted_after_the_migration_is_never_touched():
    db = SqliteDB()
    _seed_row(db, version=7, by="operator")                   # no seed row: nothing to migrate
    assert production.drop_former_seeds(db) == []
    assert db.execute("SELECT COUNT(*) FROM flow_version WHERE name = 'invite_join'")[0][0] == 1
