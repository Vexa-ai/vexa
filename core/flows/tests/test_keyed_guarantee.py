"""ADR-0052 §7 — the keyed schedule a guarantee is built on (slice s2a, Vexa-ai/vexa#1962).

* a row with a deadline is missed only past it, so a worker down from the fire time almost to the
  deadline still fires when it returns;
* an unchanged re-upsert (a duplicate delivery) does not extend the window a row may fire in;
* rows `fire_due` skips (refused by the daily cap) never stop a later due row, and rows of a retired
  flow are marked `retired` instead of sitting in front of everyone else's;
* the shares count per flow, and a code flow with `share: account` has no company total;
* a full share is answered to the caller, not only logged;
* `invite_join` is registered in code; its stored seed copy is removed once.

Review of Vexa-ai/vexa#1996:

* a row is its person's: two accounts whose events carry one key hold two rows and get two fires;
* a person's own flows share one total, and a deadline lies at most an hour after the fire time;
* a rollback to the image that still seeds `invite_join` finds a version of it to run."""
from __future__ import annotations

import calendar
import json
import logging

import pytest

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


def _own_flows(reg, n, uid="7"):
    return [reg.flow_by_names(name=f"mine{i}", version=1, on_event=f"mine{i}.changed",
                              step_names=["note"], params={"_trigger": TRIGGER}, scope=f"user:{uid}")
            for i in range(n)]


def test_a_persons_own_flows_share_one_total():
    """Per flow alone, a person with N flows of their own would hold N × the share (the review's
    250,000 rows at 500 flows), and every pass reads the due ones."""
    db, clock, reg = _rig()
    mine = _own_flows(reg, 3)
    for flow in mine[:2]:
        for i in range(2):
            assert keyed.upsert(db, clock, flow, key=f"k{i}", fire_at=T, event_type="x", payload={},
                                per_user=4) == "armed"
    assert keyed.upsert(db, clock, mine[2], key="k0", fire_at=T, event_type="x", payload={},
                        per_user=4) == "refused", "the third flow does not get a share of its own"
    assert keyed.upsert(db, clock, mine[0], key="k0", fire_at=T + 60, event_type="x", payload={},
                        per_user=4) == "moved", "a row already held still moves"


def test_another_persons_own_flows_are_not_in_the_total():
    db, clock, reg = _rig()
    for i, flow in enumerate(_own_flows(reg, 2)):
        assert keyed.upsert(db, clock, flow, key="k", fire_at=T, event_type="x", payload={},
                            per_user=2) == "armed"
    other = _own_flows(reg, 1, uid="8")[0]
    assert keyed.upsert(db, clock, other, key="k", fire_at=T, event_type="x", payload={},
                        per_user=2) == "armed"


HOUR = 3600.0


def test_a_deadline_lies_at_most_an_hour_after_the_fire_time():
    """A submitted trigger may name any path as its deadline; a row the daily cap keeps refusing
    stays armed and due until then, and every pass reads it again."""
    db, clock, reg = _rig(trigger={**TRIGGER, "deadline": "payload.start + 2d"})
    _book(db, reg, clock, "m1")
    at, dl = db.execute("SELECT fire_at, deadline FROM keyed_schedule")[0]
    assert dl == at + HOUR
    clock._t = at + HOUR - 1
    assert keyed.fire_due(db, reg, clock, admissible=lambda f, r: False) == 0
    assert _status(db, "m1") == "armed"
    clock._t = at + HOUR + 1
    keyed.fire_due(db, reg, clock, admissible=lambda f, r: False)
    assert _status(db, "m1") == "missed", "past the bound the refused row is missed, not read again"


def test_the_booked_bots_own_window_is_inside_the_bound():
    db, clock, reg = _rig()
    _book(db, reg, clock, "m1")
    assert db.execute("SELECT deadline FROM keyed_schedule")[0][0] == T + GRACE


# ── a row is its person's ────────────────────────────────────────────────────────────────────────

def test_two_accounts_with_one_key_hold_two_rows_and_fire_twice():
    db, clock, reg = _rig()
    _book(db, reg, clock, "m1", uid="7")
    _book(db, reg, clock, "m1", uid="8", start=T + 60)        # the same key, another account
    rows = db.execute("SELECT person, fire_at FROM keyed_schedule ORDER BY person")
    assert rows == [("7", T - LEAD), ("8", T + 60 - LEAD)], "the second never moved the first"
    clock._t = T + 60 - LEAD + 1
    assert keyed.fire_due(db, reg, clock) == 2
    uids = sorted(json.loads(r)["uid"] for (r,) in db.execute(
        "SELECT subject_refs FROM reaction WHERE event_type = 'keyed.due'"))
    assert uids == ["7", "8"]


def test_one_accounts_cancel_touches_only_its_own_row():
    db, clock, reg = _rig()
    _book(db, reg, clock, "m1", uid="7")
    _book(db, reg, clock, "m1", uid="8")
    admit(db, reg, clock, source_event_id="c8", event_type="planned.changed",
          subject_refs={"meeting_id": "m1", "start": T, "uid": "8", "status": "cancelled"})
    assert db.execute("SELECT person, status FROM keyed_schedule ORDER BY person") == [
        ("7", "armed"), ("8", "cancelled")]


def test_two_accounts_firing_in_one_second_are_two_runs():
    flow = Registry().flow(name="booked", version=1, on=EventType("x"), steps=[])
    assert keyed.slot_key(flow, "k", T, "a/b") != keyed.slot_key(flow, "b/k", T, "a")
    assert keyed.slot_key(flow, "k", T, "7") != keyed.slot_key(flow, "k", T, "8")


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
    _seed_row(db, version=3, by="operator")
    with caplog.at_level(logging.WARNING, logger="flows.production"):
        assert production.drop_former_seeds(db) == ["invite_join"]
    assert db.execute("SELECT version, status, created_by FROM flow_version "
                      "WHERE name = 'invite_join' ORDER BY version") == [
        (2, "retired", "operator"), (3, "active", "operator")], "v3 is what a rollback runs"
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "v2 (active, by operator)" in said and "v3 left active" in said


def _previous_image(db):
    """What the image before ADR-0052 §6 (`invite_join` a seed) does at its boot: `seed()` writes
    `invite_join` v1 only where the table holds no version of it, then `refresh_from_db` loads the
    ACTIVE rows. Returns the `invite_join` it would run, or None."""
    if db.execute("SELECT COUNT(*) FROM flow_version WHERE scope = 'instance' "
                  "AND name = 'invite_join'")[0][0] == 0:
        _seed_row(db)
    prev = Registry()

    @prev.step
    def ensure_user(ctx):  # noqa: ARG001 — the stored rows name it; only loading is under test
        return None
    prev.refresh_from_db(db)
    return prev.newest("instance", "invite_join")


@pytest.mark.parametrize("company", [
    [],                                                      # the seed only
    [(2, "active")],                                         # the company changed it
    [(2, "retired"), (3, "active")],                         # …twice, and retired the first change
    [(2, "retired")],                                        # …and went back to the seed
], ids=["seed-only", "one-company-version", "two-company-versions", "company-versions-retired"])
@pytest.mark.parametrize("seed_status", ["active", "retired"])
def test_a_rollback_to_the_image_that_seeds_it_still_runs_invite_join(company, seed_status):
    if seed_status == "retired" and not any(st == "active" for _, st in company):
        pytest.skip("the company turned the flow off: nothing ran before either")
    db = SqliteDB()
    _seed_row(db, status=seed_status)
    for v, st in company:
        _seed_row(db, version=v, status=st, by="operator")
    before = _previous_image(db)
    assert before is not None
    production.drop_former_seeds(db)
    reg = Registry()
    production.build(reg, db)
    reg.refresh_from_db(db)
    here = reg.newest("instance", "invite_join")
    assert here.version == 1 and ("invite_join", 1) not in reg.db_versions, "this image: the code"
    after = _previous_image(db)                               # rolled back
    assert after is not None, "a rollback finds a version of invite_join to run"
    assert after.version == before.version, "the one it ran before the upgrade"


def test_removing_the_only_version_a_rollback_could_run_is_refused_by_name(caplog):
    """The seed copy is the active version and every company version is retired: removing the copy
    would leave the image before this one nothing to run and no room to seed (CEO, 2026-11-03: fail
    loudly). Refused, named, with how to restore; asked again until the company re-activates one."""
    db = SqliteDB()
    _seed_row(db)
    _seed_row(db, version=2, status="retired", by="operator")
    _seed_row(db, version=3, status="retired", by="operator")
    refused: list = []
    with caplog.at_level(logging.ERROR, logger="flows.production"):
        assert production.drop_former_seeds(db, refusals=refused) == []
    assert [(r["refused"], r["flow"]) for r in refused] == [
        (production.FORMER_SEED_UNRUNNABLE, "invite_join")]
    assert "POST /flows/invite_join/3/activate" in refused[0]["restore"]
    assert any(r.levelno == logging.ERROR and "REFUSED former_seed_unrunnable" in r.getMessage()
               for r in caplog.records)
    assert _previous_image(db).version == 1, "the copy is kept: a rollback still runs it"
    assert production.drop_former_seeds(db, refusals=(again := [])) == [] and again, "every boot"
    db.execute("UPDATE flow_version SET status = 'active' WHERE name = 'invite_join' AND version = 3")
    assert production.drop_former_seeds(db, refusals=(after := [])) == ["invite_join"] and not after
    assert _previous_image(db).version == 3


def test_the_operator_cannot_retire_the_only_version_a_rollback_runs():
    """The same refusal at the lifecycle door: after the migration, retiring the company's last
    active `invite_join` would leave a rollback nothing to run. Activating another one is the way."""
    import os
    env = {"VEXA_FLOWS_API_KEY": "test-flows-key",
           "VEXA_FLOWS_SERVICE_KEY": "test-flows-service-key-not-a-placeholder",
           "VEXA_FLOWS_DB_URL": "postgresql+pg8000://keyed:unreachable@127.0.0.1:1/flows"}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        from flows_integrations import flows_api as fa
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    from fastapi.testclient import TestClient
    saved_db, fa.db = fa.db, SqliteDB()
    try:
        _seed_row(fa.db, version=2, by="operator")
        client, op = TestClient(fa.app, raise_server_exceptions=False), {
            "X-Flows-Operator-Key": fa.API_KEY}
        r = client.post("/flows/invite_join/2/retire", params={"scope": "instance"}, headers=op)
        assert r.status_code == 409 and r.json()["detail"]["refused"] == "former_seed_unrunnable"
        assert fa.db.execute("SELECT status FROM flow_version WHERE version = 2") == [("active",)]
        _seed_row(fa.db, version=3, status="retired", by="operator")
        r = client.post("/flows/invite_join/3/activate", params={"scope": "instance"}, headers=op)
        assert r.status_code == 200, r.text
        assert fa.db.execute("SELECT version, status FROM flow_version ORDER BY version") == [
            (2, "retired"), (3, "active")]
    finally:
        fa.db = saved_db


def test_this_image_never_chooses_a_stored_version_of_a_flow_the_code_owns(caplog):
    db = SqliteDB()
    _seed_row(db, version=9, by="operator")
    reg = Registry()
    production.build(reg, db)
    with caplog.at_level(logging.WARNING, logger="flows.registry"):
        reg.refresh_from_db(db)
        reg.refresh_from_db(db)
    assert reg.newest("instance", "invite_join").version == 1
    said = [r.getMessage() for r in caplog.records if "invite_join@9" in r.getMessage()]
    assert len(said) == 1, "said once, not on every refresh"


def test_a_version_submitted_after_the_migration_is_never_touched():
    db = SqliteDB()
    _seed_row(db, version=7, by="operator")                   # no seed row: nothing to migrate
    assert production.drop_former_seeds(db) == []
    assert db.execute("SELECT COUNT(*) FROM flow_version WHERE name = 'invite_join'")[0][0] == 1
