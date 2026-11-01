"""The hold pair: `claim` and `release` on a waiting reaction (`flows.signals.claim_hold`).

What it closes: a row someone outside the engine is working on (an agent session on the obligation
the row stands for) read as waiting, so a board counting `running` showed 0 while four were busy.

Engine half first, on the sqlite double with rows written the way the engine writes them; then the
route, through the same offline harness as `test_subject_bearer.py` (its fixtures are reused), so
the auth is the real subject-scoped one.
"""
from __future__ import annotations

import json

import pytest

from flows import FakeClock, claim, claim_hold, reclaim, release_hold
from flows.signals import expire_holds, hold_of
from sqlite_double import SqliteDB

from test_subject_bearer import bearer, client, fa, identity, rows  # noqa: F401 — fixtures

@pytest.fixture(autouse=True)
def no_signals(rows):  # noqa: F811 — after `rows` sets up, before it tears down
    fa.db.execute("DELETE FROM signal")
    yield
    fa.db.execute("DELETE FROM signal")


T0 = 1_788_000_000.0
LATER = T0 + 7 * 86400


def _row(db, rid, *, status="retrying", next_run_at=LATER, blocked_deadline=None):
    db.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                        flow, flow_version, step, status, attempt, next_run_at,
                                        blocked_deadline, reason, created_at, updated_at)
                  VALUES (:rid,:sid,'factory.item',:refs,'factory_obligation',1,'obligation_hold',
                          :st,1,:nra,:bdl,NULL,:c,:c)""",
               {"rid": rid, "sid": f"{rid}::factory_obligation",
                "refs": json.dumps({"ref": f"card:{rid}", "owner": "software", "uid": "126"}),
                "st": status, "nra": next_run_at, "bdl": blocked_deadline, "c": T0})


def _state(db, rid):
    return db.execute("SELECT status, next_run_at, blocked_deadline, lease_until FROM reaction "
                      "WHERE reaction_id = :r", {"r": rid})[0]


def _signals(db, rid):
    return [(k, a) for k, a in db.execute(
        "SELECT kind, actor FROM signal WHERE reaction_id = :r ORDER BY created_at, kind DESC",
        {"r": rid})]


# ── engine ───────────────────────────────────────────────────────────────────────────────────────

def test_a_sleeping_row_is_claimed_and_released_back_to_where_it_was():
    db, clock = SqliteDB(), FakeClock(T0)
    _row(db, "r1")
    out = claim_hold(db, "r1", "software-worker-2", clock, lease_s=600, reason="on it")
    assert out["claimed"] and out["from"] == "retrying"
    assert _state(db, "r1")[0] == "running" and _state(db, "r1")[3] == T0 + 600
    assert hold_of(db, "r1")["holder"] == "software-worker-2"

    clock.advance(60)
    out = release_hold(db, "r1", "software-worker-2", clock, reason="PR #1990 opened")
    assert out == {"released": True, "to": "retrying"}
    assert _state(db, "r1") == ("retrying", LATER, None, None)   # the next check is kept, not "now"
    assert hold_of(db, "r1") is None
    assert _signals(db, "r1") == [("claim", "software-worker-2"), ("release", "software-worker-2")]
    receipt = db.execute("SELECT reason FROM signal WHERE kind='release'")[0][0]
    assert receipt == "PR #1990 opened"


def test_a_blocked_row_keeps_its_block_deadline_through_a_hold():
    db, clock = SqliteDB(), FakeClock(T0)
    _row(db, "r1", status="blocked", next_run_at=T0, blocked_deadline=LATER)
    assert claim_hold(db, "r1", "w", clock)["claimed"]
    assert release_hold(db, "r1", "w", clock)["released"]
    assert _state(db, "r1") == ("blocked", T0, LATER, None)


def test_someone_else_holding_a_live_lease_is_refused_and_the_holder_renews():
    db, clock = SqliteDB(), FakeClock(T0)
    _row(db, "r1")
    assert claim_hold(db, "r1", "worker-a", clock, lease_s=600)["claimed"]
    other = claim_hold(db, "r1", "worker-b", clock)
    assert other == {"claimed": False, "refused": "held", "holder": "worker-a",
                     "lease_until": T0 + 600}
    clock.advance(300)
    again = claim_hold(db, "r1", "worker-a", clock, lease_s=600)
    assert again["claimed"] and again["renewed"] and _state(db, "r1")[3] == T0 + 900
    # the renewal keeps the ORIGINAL waiting state, not `running`
    assert release_hold(db, "r1", "worker-a", clock)["to"] == "retrying"
    assert _state(db, "r1")[0] == "retrying"


def test_terminal_rows_and_rows_the_worker_runs_are_refused():
    db, clock = SqliteDB(), FakeClock(T0)
    for st in ("done", "failed", "cancelled"):
        _row(db, f"r-{st}", status=st)
        assert claim_hold(db, f"r-{st}", "w", clock) == {
            "claimed": False, "refused": "terminal", "status": st}
        assert _state(db, f"r-{st}")[0] == st
    _row(db, "r-due", next_run_at=T0 - 1)
    assert claim(db, clock).reaction_id == "r-due"            # the engine's own worker took it
    assert claim_hold(db, "r-due", "w", clock)["refused"] == "running"
    assert claim_hold(db, "nope", "w", clock)["refused"] == "missing"


def test_a_held_row_is_never_taken_by_the_worker():
    db, clock = SqliteDB(), FakeClock(T0)
    _row(db, "r1", next_run_at=T0 - 1)                        # due now
    assert claim_hold(db, "r1", "w", clock)["claimed"]
    assert claim(db, clock) is None


def test_an_expired_hold_returns_to_its_waiting_state_on_the_reconciler_pass():
    """Not to the queue as due: that is what a dead WORKER's lease means, and a sleeping obligation
    must not wake because its holder went away."""
    db, clock = SqliteDB(), FakeClock(T0)
    _row(db, "r1")
    _row(db, "r-worker", next_run_at=T0 - 1)
    assert claim_hold(db, "r1", "w", clock, lease_s=60)["claimed"]
    assert claim(db, clock, lease_s=60).reaction_id == "r-worker"
    clock.advance(61)
    reclaim(db, clock)
    assert _state(db, "r1") == ("retrying", LATER, None, None)
    assert _state(db, "r-worker")[0] == "retrying" and _state(db, "r-worker")[1] == T0 + 61
    assert _signals(db, "r1")[-1] == ("release", "lease")
    assert expire_holds(db, clock) == 0                       # idempotent


def test_a_lapsed_hold_can_be_taken_by_the_next_claimer():
    db, clock = SqliteDB(), FakeClock(T0)
    _row(db, "r1")
    assert claim_hold(db, "r1", "worker-a", clock, lease_s=60)["claimed"]
    clock.advance(61)
    out = claim_hold(db, "r1", "worker-b", clock)
    assert out["claimed"] and out["from"] == "retrying"
    assert release_hold(db, "r1", "worker-b", clock)["to"] == "retrying"


def test_release_without_a_hold_and_by_the_wrong_holder_is_refused():
    db, clock = SqliteDB(), FakeClock(T0)
    _row(db, "r1")
    assert release_hold(db, "r1", "w", clock) == {"released": False, "refused": "not_held"}
    claim_hold(db, "r1", "worker-a", clock)
    assert release_hold(db, "r1", "x", clock, holder="worker-b")["refused"] == "held"
    assert _state(db, "r1")[0] == "running"


def test_cancel_still_ends_a_held_row():
    from flows import cancel
    db, clock = SqliteDB(), FakeClock(T0)
    _row(db, "r1")
    claim_hold(db, "r1", "w", clock)
    assert cancel(db, "r1", "owner", clock, reason="dropped")
    assert _state(db, "r1")[0] == "cancelled"
    assert release_hold(db, "r1", "w", clock)["refused"] == "not_held"


# ── the route: same auth as the other verbs ──────────────────────────────────────────────────────

def test_claim_and_release_over_http_by_the_owner(client, identity):  # noqa: F811
    r = client.post("/reactions/r-anna-uid/claim", headers={**bearer("anna-key"),
                                                             "X-Actor": "software-worker-1"},
                    json={"lease_s": 900, "reason": "taking it"})
    assert r.status_code == 200, r.text
    assert r.json()["claim"] is True and r.json()["holder"] == "software-worker-1"
    assert fa.db.execute(
        "SELECT status FROM reaction WHERE reaction_id='r-anna-uid'")[0][0] == "running"

    r = client.post("/reactions/r-anna-uid/claim", headers={**bearer("anna-key"),
                                                             "X-Actor": "software-worker-2"})
    assert r.status_code == 409 and r.json()["detail"]["holder"] == "software-worker-1"

    r = client.post("/reactions/r-anna-uid/release", headers={**bearer("anna-key"),
                                                               "X-Actor": "software-worker-1"},
                    json={"reason": "PR opened"})
    assert r.status_code == 200 and r.json() == {"release": True, "released": True,
                                                 "to": "admitted"}
    assert fa.db.execute(
        "SELECT status FROM reaction WHERE reaction_id='r-anna-uid'")[0][0] == "admitted"


def test_the_holder_defaults_to_the_callers_subject(client, identity):  # noqa: F811
    r = client.post("/reactions/r-anna-uid/claim", headers=bearer("anna-key"))
    assert r.status_code == 200 and r.json()["holder"] == "126"


def test_claiming_someone_elses_reaction_is_refused(client, identity):  # noqa: F811
    r = client.post("/reactions/r-ben/claim", headers=bearer("anna-key"), json={})
    assert r.status_code == 403
    assert fa.db.execute("SELECT status FROM reaction WHERE reaction_id='r-ben'")[0][0] == "admitted"


def test_claim_without_a_credential_is_refused(client, identity):  # noqa: F811
    assert client.post("/reactions/r-anna-uid/claim", json={}).status_code == 401


# ── the migration: an existing database's `signal_kind` gains the two kinds, once ────────────────

def _recorder(has_claim):
    seen = []

    def execute(sql, params=None):
        seen.append(sql)
        if "pg_constraint" in sql:
            return [(1 if has_claim else 0,)]
        if "key_column_usage" in sql:
            return [(1,)]
        return []
    return seen, execute


def test_the_migration_widens_signal_kind_on_an_old_database_only():
    from flows import migrations
    seen, execute = _recorder(has_claim=False)
    migrations.apply(execute)
    assert list(migrations.SWAP_SIGNAL_KIND) == [s for s in seen if "signal_kind" in s
                                                  and "pg_constraint" not in s]
    seen, execute = _recorder(has_claim=True)
    migrations.apply(execute)
    assert not [s for s in seen if s in migrations.SWAP_SIGNAL_KIND]
