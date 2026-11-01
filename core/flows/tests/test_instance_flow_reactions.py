"""`GET /flows/{name}/reactions`: an instance flow's reactions for the operator, with the live hold
and how each closed one was ended.

What it closes: the factory board read Flows straight from Postgres through a SELECT-only role,
because no route returned a reaction's refs, its hold or its close receipt (audit 1 Nov 2026,
Layer 0). A board over the instance's queue now reads the product API only.
"""
from __future__ import annotations

import json

import pytest

from flows import FakeClock, cancel, claim_hold, resume

from test_subject_bearer import admin as operator, bearer, client, fa, identity, rows  # noqa: F401 — fixtures


T0 = 1_788_000_000.0


@pytest.fixture(autouse=True)
def clean(rows):  # noqa: F811
    for t in ("signal", "effect_receipt"):
        fa.db.execute(f"DELETE FROM {t}")
    fa.db.execute("DELETE FROM reaction WHERE flow IN ('factory_obligation', 'user:126/mine')")
    yield
    for t in ("signal", "effect_receipt"):
        fa.db.execute(f"DELETE FROM {t}")
    fa.db.execute("DELETE FROM reaction WHERE flow IN ('factory_obligation', 'user:126/mine')")


def _row(rid, *, status="retrying", flow="factory_obligation", updated=T0):
    fa.db.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                           flow, flow_version, step, status, attempt, next_run_at,
                                           blocked_deadline, reason, created_at, updated_at)
                     VALUES (:rid,:sid,'factory.item',:refs,:flow,1,'obligation_hold',
                             :st,1,:nra,NULL,'waits',:c,:u)""",
                  {"rid": rid, "sid": f"{rid}::{flow}", "flow": flow, "st": status,
                   "refs": json.dumps({"ref": f"card:{rid}", "uid": "126"}),
                   "nra": T0 + 3600, "c": T0, "u": updated})


def _get(client, path):  # noqa: F811
    r = client.get(path, headers=operator())
    assert r.status_code == 200, r.text
    return {x["id"]: x for x in r.json()["reactions"]}


def test_open_rows_carry_refs_and_the_live_hold(client):  # noqa: F811
    _row("r-wait")
    _row("r-held")
    clock = FakeClock(T0 + 10)
    assert claim_hold(fa.db, "r-held", "s1-worker@bbb:~/ci/s1", clock, lease_s=600)["claimed"]
    got = _get(client, "/flows/factory_obligation/reactions")
    assert set(got) == {"r-wait", "r-held"}
    assert got["r-wait"]["refs"] == {"ref": "card:r-wait", "uid": "126"}
    assert got["r-wait"]["hold"] is None and got["r-wait"]["hold_moved_at"] is None
    held = got["r-held"]
    assert held["status"] == "running" and held["lease_until"] == T0 + 610
    assert held["hold"]["holder"] == "s1-worker@bbb:~/ci/s1"
    assert held["hold"]["claimed_at"] == T0 + 10 and held["hold_moved_at"] == T0 + 10


def test_closed_rows_name_who_ended_them_and_the_receipt(client):  # noqa: F811
    _row("r-done", status="blocked")            # resume answers a row waiting on a person
    _row("r-drop")
    _row("r-open")
    clock = FakeClock(T0 + 20)
    assert resume(fa.db, "r-done", actor="founder", clock=clock, reason="PR merged")
    fa.db.execute("UPDATE reaction SET status = 'done' WHERE reaction_id = 'r-done'")   # the step then ends
    assert cancel(fa.db, "r-drop", "business", clock, reason="no longer needed")
    got = _get(client, "/flows/factory_obligation/reactions?status=closed")
    assert set(got) == {"r-done", "r-drop"}
    assert got["r-done"]["close"] == {"verb": "done", "by": "founder", "receipt": "PR merged",
                                      "at": got["r-done"]["close"]["at"]}
    assert got["r-drop"]["close"]["verb"] == "cancel"
    assert got["r-drop"]["close"]["by"] == "business"
    assert got["r-drop"]["close"]["receipt"] == "no longer needed"


def test_since_and_one_state_filter(client):  # noqa: F811
    _row("r-old", updated=T0)
    _row("r-new", status="blocked", updated=T0 + 100)
    assert set(_get(client, f"/flows/factory_obligation/reactions?since={T0 + 50}")) == {"r-new"}
    assert set(_get(client, "/flows/factory_obligation/reactions?status=blocked")) == {"r-new"}
    r = client.get("/flows/factory_obligation/reactions?status=nonsense", headers=operator())
    assert r.status_code == 422


def test_only_the_operator_reads_and_never_a_persons_flow(client, identity):  # noqa: F811
    _row("r-mine", flow="user:126/mine")
    assert client.get("/flows/factory_obligation/reactions").status_code == 401
    r = client.get("/flows/user:126%2Fmine/reactions", headers=operator())
    assert r.status_code in (403, 405) and "r-mine" not in r.text   # a slash never reaches the route
    identity.set({"tok-anna": {"uid": "126", "email": "anna@vexa.test"}})
    r = client.get("/flows/factory_obligation/reactions", headers=bearer("tok-anna"))
    assert r.status_code == 401
