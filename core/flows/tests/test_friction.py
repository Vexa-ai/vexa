"""PRD 40.9 open-decision 8 — friction is a sink, not a domain.

Founder, 2026-09-03 09:58Z: *"friction is just a sink — we dump that somewhere in production so
that our dev agent can read it and fix the MCP and behaviour based on it."* This is the contract
for the one route that sink runs through:

  B1  POST /friction refuses a report with no session — the exact gap this carrier exists to close
  B2  POST /friction refuses a report with no what_i_tried / what_happened
  B3  a filed report is admitted as a `friction.reported` reaction and readable straight off it —
      no receipt, no worker tick, needed for it to show up
  B4  GET /friction (friction_so_far) is scoped to the caller, the same way reactions_list is
  B5  the flow this admits into is registered in every profile (no agent-domain dependency)

Offline, like `test_queue_waiting.py`: real sqlite rows, the real app through `TestClient`, no
network and no worker loop — `record_friction`'s own `Done` never has to run for a filed report to
be visible, because the read model (`flows_timeline.friction_for_subject`) reads the reaction row
itself, not a receipt.
"""
from __future__ import annotations

import os

import pytest
from sqlite_double import SqliteDB

from flows_timeline import friction_for_subject


def _identity(subject):
    return ("126", "dima@vexa.ai") if str(subject) in ("126", "dima@vexa.ai") else ("999", "")


# ── B5 · the flow exists in every profile, offline ──────────────────────────────────────────────

def test_friction_reported_has_a_registered_flow_in_every_profile():
    """Without a matching flow, `admit()` creates ZERO reaction rows (`flows/admission.py`) and a
    filed report would be admitted into nothing — the exact failure mode `record_friction`'s own
    docstring names. This is checked against the base `production` registry alone (no
    `production_agent` half), because friction has to work with no agent domain deployed."""
    from flows import Registry
    from flows_defs import production

    reg = Registry()
    production.build(reg, SqliteDB())
    matches = reg.match("friction.reported")
    assert matches, "no flow reacts to friction.reported — admit() would create nothing"
    assert {f.name for f in matches} == {"friction_log"}


# ── B3 · the read model, directly against a real reaction row ───────────────────────────────────

def _friction_row(db, *, uid="126", session="chat-abc", fid="fr_test1", severity="annoyance",
                  tried="opened the terminal", happened="the page 404'd", created=1_788_000_000.0,
                  extra=None):
    import json
    refs = {"uid": uid, "session": session, "friction_id": fid, "severity": severity,
           "what_i_tried": tried, "what_happened": happened, **(extra or {})}
    db.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                        flow, flow_version, step, status, attempt, next_run_at,
                                        created_at, updated_at)
                  VALUES (:rid,:sid,'friction.reported',:refs,'friction_log',1,'record_friction',
                          'admitted',0,0,:c,:c)""",
               {"rid": f"r-{fid}", "sid": f"friction-{fid}::friction_log",
                "refs": json.dumps(refs), "c": created})


def test_friction_for_subject_reads_straight_off_the_reaction_row_no_receipt_needed():
    db = SqliteDB()
    _friction_row(db, fid="fr_a")
    out = friction_for_subject(db, subject="126", identity=_identity)
    assert out is not None and len(out) == 1
    row = out[0]
    assert row["id"] == "fr_a"
    assert row["session"] == "chat-abc"
    assert row["tried"] == "opened the terminal"
    assert row["happened"] == "the page 404'd"
    assert row["severity"] == "annoyance"


def test_friction_for_subject_scopes_by_uid_like_list_reactions():
    db = SqliteDB()
    _friction_row(db, uid="126", fid="fr_mine")
    _friction_row(db, uid="999", fid="fr_theirs")
    mine = friction_for_subject(db, subject="126", identity=_identity)
    assert [r["id"] for r in mine] == ["fr_mine"]


def test_friction_for_subject_none_when_nobody_answers_to_the_subject():
    db = SqliteDB()
    nobody = lambda _subject: ("", "")
    assert friction_for_subject(db, subject="ghost@nowhere.test", identity=nobody) is None


def test_friction_for_subject_honours_since():
    db = SqliteDB()
    _friction_row(db, fid="fr_old", created=1_000_000_000.0)
    _friction_row(db, fid="fr_new", created=2_000_000_000.0)
    out = friction_for_subject(db, subject="126", since=1_500_000_000.0, identity=_identity)
    assert [r["id"] for r in out] == ["fr_new"]


def test_extra_context_carries_only_the_keys_that_were_present():
    db = SqliteDB()
    _friction_row(db, fid="fr_ctx", extra={"tool": "bot_send", "meeting_id": "104"})
    out = friction_for_subject(db, subject="126", identity=_identity)
    assert out[0]["context"] == {"tool": "bot_send", "meeting_id": "104"}


# ── B1/B2/B4 · the real route, through TestClient ────────────────────────────────────────────────

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-friction",
       "INTERNAL_API_SECRET": "test-internal-secret",
       "VEXA_FLOWS_DB_URL": "postgresql+psycopg://friction:unreachable@127.0.0.1:1/flows"}


@pytest.fixture(scope="module")
def api():
    """The real app, same composition `test_queue_waiting.py::api` documents: an unreachable
    Postgres DSN at import (proves the app builds with no database), then a working `SqliteDB`
    swapped in so the route's own `admit()` and `friction_for_subject` genuinely execute."""
    from fastapi.testclient import TestClient

    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    try:
        from flows_integrations import flows_api
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    flows_api.db = SqliteDB()
    # `flows_integrations.flows_api` is a SINGLETON module: whichever test file imports it first in
    # this process wins, and every later `import` is a cache hit that ignores `_ENV` entirely — the
    # isolation `test_queue_waiting.py`'s own fixture docstring names. So the operator key this
    # fixture's requests must present is whatever `flows_api.API_KEY` actually ended up holding,
    # read back off the live module, never assumed to be `_ENV`'s own value.
    return flows_api, TestClient(flows_api.app)


def _clear(flows_api):
    flows_api.db.execute("DELETE FROM reaction")


def _headers(flows_api, uid="126"):
    return {"X-Flows-Operator-Key": flows_api.API_KEY, "X-User-Id": uid}


def _post(flows_api, client, **kw):
    uid = kw.pop("uid", "126")
    return client.post("/friction", params=kw, headers=_headers(flows_api, uid))


def test_a_report_with_no_session_is_refused(api):
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, what_i_tried="x", what_happened="y", severity="annoyance")
    assert r.status_code == 400
    assert "session" in r.json()["detail"]


def test_a_report_with_no_what_i_tried_or_what_happened_is_refused(api):
    flows_api, client = api
    _clear(flows_api)
    assert _post(flows_api, client, session="s1", what_happened="y",
                severity="annoyance").status_code == 400
    assert _post(flows_api, client, session="s1", what_i_tried="x",
                severity="annoyance").status_code == 400


def test_a_report_with_an_unknown_severity_is_refused(api):
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, session="s1", what_i_tried="x", what_happened="y",
             severity="urgent!!")
    assert r.status_code == 400


def test_the_bare_operator_key_with_no_stamped_identity_is_refused(api):
    """No credential to attribute the report to — refused, per `report_friction`'s own docstring,
    rather than filed anonymously."""
    flows_api, client = api
    _clear(flows_api)
    r = client.post("/friction", params={"session": "s1", "what_i_tried": "x",
                                        "what_happened": "y", "severity": "annoyance"},
                    headers={"X-Flows-Operator-Key": flows_api.API_KEY})
    assert r.status_code == 401


def test_a_well_formed_report_is_admitted_and_immediately_readable(api):
    flows_api, client = api
    _clear(flows_api)
    posted = _post(flows_api, client, session="chat-42", what_i_tried="asked for a transcript",
                   what_happened="got a 404", severity="blocker", tool="meeting_transcript")
    assert posted.status_code == 201
    body = posted.json()
    assert body["recorded"] is True and body["id"].startswith("fr_")

    got = client.get("/friction", headers=_headers(flows_api))
    assert got.status_code == 200
    reports = got.json()["reports"]
    assert len(reports) == 1
    assert reports[0]["id"] == body["id"]
    assert reports[0]["session"] == "chat-42"
    assert reports[0]["context"]["tool"] == "meeting_transcript"


def test_friction_so_far_is_scoped_to_the_caller_not_the_instance(api):
    flows_api, client = api
    _clear(flows_api)
    _post(flows_api, client, uid="126", session="s-mine", what_i_tried="a", what_happened="b",
         severity="annoyance")
    _post(flows_api, client, uid="999", session="s-theirs", what_i_tried="a", what_happened="b",
         severity="annoyance")
    mine = client.get("/friction", headers=_headers(flows_api, "126")).json()
    assert [r["session"] for r in mine["reports"]] == ["s-mine"]


def test_friction_is_refused_with_no_credential_at_all(api):
    flows_api, client = api
    _clear(flows_api)
    r = client.post("/friction", params={"session": "s1", "what_i_tried": "x",
                                        "what_happened": "y", "severity": "annoyance"})
    assert r.status_code == 401
    assert client.get("/friction").status_code == 401


# ── #1510's C3 — friction.fixed, the close-out half ─────────────────────────────────────────────

def test_friction_fixed_has_a_registered_flow_in_every_profile():
    from flows import Registry
    from flows_defs import production

    reg = Registry()
    production.build(reg, SqliteDB())
    matches = reg.match("friction.fixed")
    assert matches, "no flow reacts to friction.fixed — admit() would create nothing"
    assert {f.name for f in matches} == {"friction_fix"}


def _fixed_row(db, *, fid: str, fix_ref: str = "PR #1409", created=1_788_100_000.0):
    import json
    refs = {"friction_id": fid, "fix_ref": fix_ref}
    db.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                        flow, flow_version, step, status, attempt, next_run_at,
                                        created_at, updated_at)
                  VALUES (:rid,:sid,'friction.fixed',:refs,'friction_fix',1,
                          'record_friction_fixed','admitted',0,0,:c,:c)""",
               {"rid": f"rfix-{fid}", "sid": f"friction-fix-{fid}",
                "refs": json.dumps(refs), "c": created})


def test_friction_for_subject_folds_a_fixed_row_into_status():
    db = SqliteDB()
    _friction_row(db, fid="fr_a")
    _friction_row(db, fid="fr_b")
    _fixed_row(db, fid="fr_a")
    out = {r["id"]: r["status"] for r in friction_for_subject(db, subject="126", identity=_identity)}
    assert out == {"fr_a": "fixed", "fr_b": "open"}


def test_a_report_with_no_matching_fix_stays_open():
    db = SqliteDB()
    _friction_row(db, fid="fr_lonely")
    out = friction_for_subject(db, subject="126", identity=_identity)
    assert out[0]["status"] == "open"


def test_the_operator_with_no_x_user_id_gets_the_whole_instance_not_a_400(api):
    """`friction_for_subject`'s own docstring has always promised this ("the whole-instance view
    stays behind the caller's own authorization"); the route never actually let an operator reach
    it until #1510's C2/C3 needed it for the rig's `friction_dump`."""
    flows_api, client = api
    _clear(flows_api)
    _post(flows_api, client, uid="126", session="s-a", what_i_tried="a", what_happened="b",
         severity="annoyance")
    _post(flows_api, client, uid="999", session="s-b", what_i_tried="a", what_happened="b",
         severity="annoyance")
    r = client.get("/friction", headers={"X-Flows-Operator-Key": flows_api.API_KEY})
    assert r.status_code == 200
    assert sorted(x["session"] for x in r.json()["reports"]) == ["s-a", "s-b"]


def test_friction_fixed_requires_the_operator_key(api):
    flows_api, client = api
    r = client.post("/friction/fr_x/fix", params={"fix_ref": "PR #1"})
    assert r.status_code == 401


def test_friction_fixed_requires_a_fix_ref(api):
    flows_api, client = api
    r = client.post("/friction/fr_x/fix", params={"fix_ref": ""},
                    headers={"X-Flows-Operator-Key": flows_api.API_KEY})
    assert r.status_code == 400


def test_friction_fixed_closes_a_report_filed_through_any_producer(api):
    """A3: a report filed through the flows-native route (this fixture's own `_post`) closes
    exactly the way one filed through agent-api's forward or the rig would — the route does not
    care where the friction_id came from, only that it names one."""
    flows_api, client = api
    _clear(flows_api)
    posted = _post(flows_api, client, uid="126", session="s1", what_i_tried="a",
                   what_happened="b", severity="annoyance")
    fid = posted.json()["id"]

    fix = client.post(f"/friction/{fid}/fix", params={"fix_ref": "PR #1410"},
                      headers={"X-Flows-Operator-Key": flows_api.API_KEY})
    assert fix.status_code == 201
    assert fix.json() == {"id": fid, "status": "fixed", "fix_ref": "PR #1410"}

    got = client.get("/friction", headers=_headers(flows_api, "126")).json()
    assert got["reports"][0]["status"] == "fixed"


def test_fixing_the_same_id_twice_is_a_no_op_not_two_events(api):
    flows_api, client = api
    _clear(flows_api)
    posted = _post(flows_api, client, session="s1", what_i_tried="a", what_happened="b",
                   severity="annoyance")
    fid = posted.json()["id"]
    hdr = {"X-Flows-Operator-Key": flows_api.API_KEY}
    client.post(f"/friction/{fid}/fix", params={"fix_ref": "PR #1"}, headers=hdr)
    client.post(f"/friction/{fid}/fix", params={"fix_ref": "PR #1"}, headers=hdr)
    n = flows_api.db.execute(
        "SELECT COUNT(*) FROM reaction WHERE event_type = 'friction.fixed'")[0][0]
    assert n == 1
