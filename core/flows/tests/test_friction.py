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
  B6  NO VOCABULARY WORD COSTS A REPORT — an unknown `kind` is stored as `other` and an unknown
      `severity` as `annoyance`, each with the caller's own word kept beside it (F-D26)
  B7  the route TELLS an agent the vocabulary: both enums reach the OpenAPI the MCP tool schema is
      derived from, and the docstring reads as instructions rather than as the route's own title

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


# ── B6 · lenient, never lossy (F-D26) ────────────────────────────────────────────────────────────
#
# On prod, 2026-09-04, twelve reports were thrown away in twenty minutes: the tool schema published
# `kind` as an open string with no allowed values, the agent filing them guessed "missing", "broke"
# and "confusing", and this route answered 400. A sink that drops a report for a spelling is the one
# thing it must never do — so a word we do not know is now STORED, under `other`, with the caller's
# own word kept beside it. The severity test below used to assert the 400; it now asserts the keep.

def test_the_three_words_prod_actually_sent_are_stored_as_other_not_refused(api):
    """The exact three kinds from the 2026-09-04 loss. Each one is a report we now keep."""
    flows_api, client = api
    for word in ("missing", "broke", "confusing"):
        _clear(flows_api)
        r = _post(flows_api, client, session="s-fd26", what_i_tried="asked for the transcript",
                 what_happened="nothing came back", severity="blocker", kind=word)
        assert r.status_code == 201, f"{word!r} was refused — F-D26 all over again"
        body = r.json()
        assert body["recorded"] is True
        assert body["kind"] == "other" and body["kind_raw"] == word

        stored = client.get("/friction", headers=_headers(flows_api)).json()["reports"]
        assert len(stored) == 1, f"{word!r} filed but not stored"
        assert stored[0]["context"]["kind"] == "other"
        assert stored[0]["context"]["kind_raw"] == word


def test_a_kind_in_the_vocabulary_is_stored_exactly_as_it_was_sent(api):
    """Leniency must not blur the words we DO know — and a known word carries no `kind_raw`."""
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, session="s-ok", what_i_tried="x", what_happened="y",
             severity="papercut", kind="missing-tool")
    assert r.status_code == 201
    assert r.json()["kind"] == "missing-tool" and "kind_raw" not in r.json()
    stored = client.get("/friction", headers=_headers(flows_api)).json()["reports"][0]
    assert stored["context"]["kind"] == "missing-tool"
    assert "kind_raw" not in stored["context"]


def test_an_unknown_severity_is_kept_too_not_refused(api):
    """Same rule, same reason: severity is the other closed vocabulary on this route."""
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, session="s1", what_i_tried="x", what_happened="y",
             severity="urgent!!")
    assert r.status_code == 201
    assert r.json()["severity"] == "annoyance" and r.json()["severity_raw"] == "urgent!!"
    stored = client.get("/friction", headers=_headers(flows_api)).json()["reports"][0]
    assert stored["severity"] == "annoyance"
    assert stored["context"]["severity_raw"] == "urgent!!"


def test_no_vocabulary_word_can_produce_a_400(api):
    """The general form of the rule, not just the three observed words: whatever an agent invents
    for either field, the report is filed. Only missing CONTENT is refusable."""
    flows_api, client = api
    for word in ("", "OTHER", "  Error  ", "kind-we-never-heard-of", "1", "🤷"):
        _clear(flows_api)
        r = _post(flows_api, client, session="s", what_i_tried="a", what_happened="b",
                 severity=word or "annoyance", kind=word)
        assert r.status_code == 201, f"{word!r} was refused"
        assert client.get("/friction", headers=_headers(flows_api)).json()["count"] == 1


def test_a_known_word_in_the_wrong_case_is_canonicalised_not_kept_as_raw(api):
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, session="s", what_i_tried="a", what_happened="b",
             severity="BLOCKER", kind="  UX ")
    assert r.json()["kind"] == "ux" and "kind_raw" not in r.json()
    assert r.json()["severity"] == "blocker" and "severity_raw" not in r.json()


# ── B7 · the tool tells the agent the vocabulary ────────────────────────────────────────────────
#
# The other half of F-D26: the route was lenient nowhere and instructive nowhere either. What the
# MCP edge publishes as `report_friction` is DERIVED from this route's OpenAPI (see
# `core/meetings/services/mcp/src/vexa_mcp/bind.py`), so the vocabulary and the instructions have to
# be here or they do not exist anywhere an agent can read them.

def _friction_op(flows_api, method="post"):
    return flows_api.app.openapi()["paths"]["/friction"][method]


def _param(op, name):
    return next(p for p in op["parameters"] if p["name"] == name)


def test_the_route_publishes_the_kind_vocabulary_in_its_openapi_schema(api):
    flows_api, _ = api
    schema = _param(_friction_op(flows_api), "kind")["schema"]
    assert schema.get("enum") == list(flows_api.FRICTION_KINDS)


def test_the_route_publishes_the_severity_vocabulary_too(api):
    flows_api, _ = api
    schema = _param(_friction_op(flows_api), "severity")["schema"]
    assert schema.get("enum") == list(flows_api.FRICTION_SEVERITIES)


def test_every_kind_carries_an_example_in_the_argument_description(api):
    """An enum an agent cannot interpret is eight words it still has to guess between."""
    flows_api, _ = api
    text = _param(_friction_op(flows_api), "kind")["schema"]["description"]
    assert set(flows_api.FRICTION_KIND_HELP) == set(flows_api.FRICTION_KINDS)
    for kind in flows_api.FRICTION_KINDS:
        assert f"`{kind}`" in text, f"{kind} is in the enum with nothing said about it"


def test_the_route_description_reads_as_instructions_not_as_a_title(api):
    """F-D12/F-D26: the MCP tool's description IS this docstring. `Report Friction` — the title
    FastAPI synthesises from the function name — is what an agent was given instead."""
    flows_api, _ = api
    op = _friction_op(flows_api)
    body = op.get("description") or ""
    assert len(body) > 400, "the docstring is the tool description; a title is not instructions"
    assert body.strip().lower() != (op.get("summary") or "").strip().lower()
    for cue in ("what_i_tried", "what_happened", "session", "blocker", "missing-tool"):
        assert cue in body, f"an agent reading this is not told about {cue}"


def test_every_flows_tool_the_manifest_publishes_says_more_than_its_own_name(api):
    """The defect CLASS, not the instance: any route in `mcp.tools.v1.json` with no docstring
    publishes an MCP tool described by nothing but its own title."""
    import json as _json
    import pathlib

    flows_api, _ = api
    manifest = _json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "mcp.tools.v1.json").read_text())
    spec = flows_api.app.openapi()
    thin = []
    for tool in manifest["tools"]:
        route = tool["route"]
        op = spec["paths"][route["path"]][route["method"].lower()]
        body = (op.get("description") or "").strip()
        if len(body) < 120:
            thin.append((tool["name"], op.get("summary"), len(body)))
    assert not thin, f"tools described by little more than their own title: {thin}"


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
