"""PRD 40.9 open-decision 8 — friction is a sink, not a domain.

Founder, 2026-09-03 09:58Z: *"friction is just a sink — we dump that somewhere in production so
that our dev agent can read it and fix the MCP and behaviour based on it."* This is the contract
for the one route that sink runs through:

  B1  POST /friction STORES a report with no session and reads it back as "no session" (F-D27 —
      this bullet used to say "refuses"; prod refused a report for having no session, and the
      report it refused was the one describing that refusal)
  B2  POST /friction stores a report with no what_i_tried / what_happened, same rule, same reason
  B2b NO VALUE A CALLER SENDS OR OMITS PRODUCES A 400 — the general form B1 and B2 are instances
      of, checked field-by-field, plus the 422 door above the handler held shut (nothing typed as
      anything but a plain unconstrained string). The only refusal left on this route is 401.
  B3  a filed report is admitted as a `friction.reported` reaction and readable straight off it —
      no receipt, no worker tick, needed for it to show up
  B4  GET /friction (friction_so_far) is scoped to the caller, the same way reactions_list is
  B5  the flow this admits into is registered in every profile (no agent-domain dependency)
  B6  NO WORD COSTS A REPORT — `kind` and `severity` are stored AS SENT, free text, and an
      argument the route does not name is kept with the report rather than dropped (F-D26 +
      the founder's ruling of 2026-09-04: "catch all signal … rich data, not too structured")
  B7  the route SUGGESTS a vocabulary and never imposes one: the words reach the OpenAPI the MCP
      tool schema is derived from, and the docstring reads as instructions, not as a title

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


def test_a_report_with_no_session_is_stored_and_reads_back_as_no_session(api):
    """B1, INVERTED BY F-D27. Prod, 2026-09-04 11:0xZ: this route answered 400 *"session is
    required — … A report with no session cannot be tied back to the conversation that produced
    it"* — and the report it refused was the one describing that refusal. A missing session is
    signal too, and the join key is OUR want, so we eat its absence rather than bill the reporter
    for it."""
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, what_i_tried="x", what_happened="y", severity="annoyance")
    assert r.status_code == 201, "a report with no session was refused — F-D27 all over again"
    assert r.json()["recorded"] is True and r.json()["session"] == ""

    reports = client.get("/friction", headers=_headers(flows_api)).json()["reports"]
    assert len(reports) == 1, "filed but not stored"
    assert reports[0]["id"] == r.json()["id"]
    assert reports[0]["tried"] == "x" and reports[0]["happened"] == "y"
    # The human field SAYS the absence; the machine field stays honestly empty. A reader must not
    # have to know that "" means "none" — and a grep for a session id must not match this row.
    assert reports[0]["session"] == "no session"
    assert reports[0]["session_id"] == ""


def test_a_session_less_report_stores_no_session_ref_at_all(api):
    """Not `session: ""`. An empty string is a value, and a later reader cannot tell it apart from
    a session whose id is genuinely empty — so the ref is absent, and absence is what it means."""
    import json as _json

    flows_api, client = api
    _clear(flows_api)
    _post(flows_api, client, what_i_tried="x", what_happened="y")
    rows = flows_api.db.execute(
        "SELECT subject_refs FROM reaction WHERE event_type = 'friction.reported'")
    assert len(rows) == 1
    refs = _json.loads(rows[0][0])
    assert "session" not in refs, f"an empty session was written into the refs: {refs!r}"


def test_a_report_with_no_text_at_all_is_still_stored(api):
    """B2, INVERTED BY F-D27 for the same reason and by the same rule. `what_i_tried` and
    `what_happened` used to 400 when either was blank. A reporter that filed at all is telling us
    something; an empty report is a thinner datum than a full one, never a rejectable one."""
    flows_api, client = api
    for kw in ({"session": "s1", "what_happened": "y"},
               {"session": "s1", "what_i_tried": "x"},
               {"session": "s1"},
               {}):
        _clear(flows_api)
        r = _post(flows_api, client, severity="annoyance", **kw)
        assert r.status_code == 201, f"{kw!r} was refused"
        assert client.get("/friction", headers=_headers(flows_api)).json()["count"] == 1


def test_the_route_answers_no_400_to_any_value_a_caller_can_send(api):
    """THE GENERAL FORM, and the point of F-D27: the audit is not "session is now optional", it is
    that this route has no refusal left that is about a caller-supplied VALUE. Absent, blank,
    over-long, unknown, non-ascii — every one is stored. The only refusal is authentication."""
    flows_api, client = api
    ugly = ("", " ", "a" * 5000, "🤷", "../../etc/passwd", "'; DROP TABLE reaction; --", "1")
    for field in ("session", "what_i_tried", "what_happened", "severity", "kind", "meeting_id",
                  "tool", "deployment", "worker_image"):
        for word in ugly:
            _clear(flows_api)
            r = _post(flows_api, client, **{field: word})
            assert r.status_code == 201, f"{field}={word!r} produced {r.status_code}"


def test_no_argument_is_typed_so_that_fastapi_refuses_before_the_route_does(api):
    """A 422 is a 400 with somebody else's name on it. If any argument here were annotated as an
    `int`/`enum`/constrained string, pydantic would refuse the call one layer ABOVE the handler and
    every leniency below would be unreachable — so the OpenAPI is asserted to publish each one as a
    plain unconstrained string. This is the same defect class as the `enum` B7 keeps out of `kind`,
    one layer up."""
    flows_api, _ = api
    op = _friction_op(flows_api)
    gates = ("enum", "pattern", "minLength", "maxLength", "minimum", "maximum", "const")
    for p in op["parameters"]:
        schema = p["schema"]
        assert schema.get("type") == "string", f"{p['name']} is not a plain string: {schema}"
        assert not [g for g in gates if g in schema], f"{p['name']} carries a gate: {schema}"
        assert not p.get("required"), f"{p['name']} is required — nothing on this route may be"


# ── B6 · catch all signal (F-D26 + the founder's ruling) ────────────────────────────────────────
#
# On prod, 2026-09-04, twelve reports were thrown away in twenty minutes: the tool schema published
# `kind` as an open string with no allowed values, the agent filing them guessed "missing", "broke"
# and "confusing", and this route answered 400. FOUNDER RULING the same morning: *"we want to catch
# all signal, does not make sense being strict about it, we want rich data, does not have to be too
# structured."* So `kind` and `severity` are stored AS SENT — no bucket, no canonical value, no
# second field holding the raw word — and any argument the route does not name is kept too. The
# severity test below used to assert a 400; it now asserts the keep.

def test_the_three_words_prod_actually_sent_are_stored_as_sent(api):
    """The exact three kinds from the 2026-09-04 loss. Each is a report we now keep, in its own
    words: `broke` is stored as `broke`, not translated into one of ours."""
    flows_api, client = api
    for word in ("missing", "broke", "confusing"):
        _clear(flows_api)
        r = _post(flows_api, client, session="s-fd26", what_i_tried="asked for the transcript",
                 what_happened="nothing came back", severity="blocker", kind=word)
        assert r.status_code == 201, f"{word!r} was refused — F-D26 all over again"
        body = r.json()
        assert body["recorded"] is True and body["kind"] == word

        stored = client.get("/friction", headers=_headers(flows_api)).json()["reports"]
        assert len(stored) == 1, f"{word!r} filed but not stored"
        assert stored[0]["context"]["kind"] == word


def test_a_word_from_the_suggested_list_is_stored_the_same_way(api):
    """There is no privileged path: a suggested word is stored exactly like an invented one."""
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, session="s-ok", what_i_tried="x", what_happened="y",
             severity="papercut", kind="missing-tool")
    assert r.status_code == 201 and r.json()["kind"] == "missing-tool"
    assert client.get("/friction", headers=_headers(flows_api)).json()[
        "reports"][0]["context"]["kind"] == "missing-tool"


def test_an_unknown_severity_is_kept_as_sent_not_refused(api):
    """Same rule, same reason: severity is the other field that used to be a closed vocabulary."""
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, session="s1", what_i_tried="x", what_happened="y",
             severity="urgent!!")
    assert r.status_code == 201 and r.json()["severity"] == "urgent!!"
    assert client.get("/friction", headers=_headers(flows_api)).json()[
        "reports"][0]["severity"] == "urgent!!"


def test_the_reporter_s_own_casing_survives(api):
    """Lowercasing is a taxonomy decision too — a small one, and still not ours to make at the
    door. `UX` and `ux` may be the same thing; whoever groups them can decide that with both in
    front of them."""
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, session="s", what_i_tried="a", what_happened="b",
             severity="BLOCKER", kind="  UX ")
    assert r.json()["kind"] == "UX" and r.json()["severity"] == "BLOCKER"


def test_no_word_in_either_field_can_produce_a_400(api):
    """The general form for the two vocabulary fields: whatever an agent invents, the report is
    filed. (F-D27 removed the last carve-out this docstring used to name — missing content is not
    refusable either; see `test_the_route_answers_no_400_to_any_value_a_caller_can_send`.)"""
    flows_api, client = api
    for word in ("", "OTHER", "kind-we-never-heard-of", "1", "🤷", "a" * 500):
        _clear(flows_api)
        r = _post(flows_api, client, session="s", what_i_tried="a", what_happened="b",
                 severity=word or "annoyance", kind=word)
        assert r.status_code == 201, f"{word!r} was refused"
        assert client.get("/friction", headers=_headers(flows_api)).json()["count"] == 1


def test_fields_this_route_never_heard_of_are_kept_with_the_report(api):
    """"Rich data, does not have to be too structured" — an argument the route does not name is
    the reporter telling us something we did not think to ask for. Dropping it silently is the
    same loss as refusing the call, just quieter."""
    flows_api, client = api
    _clear(flows_api)
    r = _post(flows_api, client, session="s", what_i_tried="a", what_happened="b",
             severity="blocker", model="haiku-4.5", request_id="req_9", retries="3")
    assert r.status_code == 201
    assert r.json()["extra"] == ["model", "request_id", "retries"]
    ctx = client.get("/friction", headers=_headers(flows_api)).json()["reports"][0]["context"]
    assert ctx["extra"] == {"model": "haiku-4.5", "request_id": "req_9", "retries": "3"}


def test_an_extra_field_cannot_re_address_the_report_to_someone_else(api):
    """`flows_timeline.model.concerns` decides whose report this is from `uid`/`subject`/`owner`
    read off the refs. Extras are namespaced under `extra` so a reporter cannot file into another
    person's queue by naming a field after one of those keys."""
    flows_api, client = api
    _clear(flows_api)
    _post(flows_api, client, uid="126", session="s", what_i_tried="a", what_happened="b",
         severity="blocker", subject="999", uid_="999", owner="999")
    mine = client.get("/friction", headers=_headers(flows_api, "126")).json()
    theirs = client.get("/friction", headers=_headers(flows_api, "999")).json()
    assert mine["count"] == 1 and theirs["count"] == 0
    assert mine["reports"][0]["context"]["extra"]["subject"] == "999"


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


def test_the_route_suggests_the_kind_words_without_making_them_a_gate(api):
    """`examples`, never `enum`. An `enum` here would be republished at the MCP edge, whose SDK
    validates a call against the tool schema before dispatching — the vocabulary would become a
    refusal one hop before the route, which is F-D26 with extra steps."""
    flows_api, _ = api
    schema = _param(_friction_op(flows_api), "kind")["schema"]
    assert schema.get("examples") == list(flows_api.FRICTION_KINDS)
    assert "enum" not in schema


def test_the_route_suggests_the_severity_words_the_same_way(api):
    flows_api, _ = api
    schema = _param(_friction_op(flows_api), "severity")["schema"]
    assert schema.get("examples") == list(flows_api.FRICTION_SEVERITIES)
    assert "enum" not in schema


def test_every_kind_carries_an_example_in_the_argument_description(api):
    """An enum an agent cannot interpret is eight words it still has to guess between."""
    flows_api, _ = api
    text = _param(_friction_op(flows_api), "kind")["schema"]["description"]
    assert set(flows_api.FRICTION_KIND_HELP) == set(flows_api.FRICTION_KINDS)
    for kind in flows_api.FRICTION_KINDS:
        assert f"`{kind}`" in text, f"{kind} is suggested with nothing said about it"
    assert "own word" in text, "the description must say the list is not a menu"


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
    """The defect CLASS, not the instance: any route in `mcp.tools.v1.json` that publishes an MCP
    tool described by nothing but its own title.

    MEASURED ON THE TEXT THE AGENT RECEIVES, not on the docstring. This used to read
    `op["description"]` alone, which silently asserted that a route's agent-facing words must be
    its DOCSTRING — and `whats_waiting` is the case that proves they must not be: an agent needs
    to be told WHEN to call a tool, and the docstring holds header precedence, operator keys and
    status codes, which answer a question an agent that never calls it does not have. So that
    route now carries its words in `summary` and no docstring at all.

    The edge composes exactly this way (`vexa_mcp/bind.py::_describe`): the description leads, and
    the summary is used when there is no description. A gate that reads one field would go on
    passing while the other one shipped a two-word title."""
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
        # What `_describe` would hand the agent, by the same rule the edge uses.
        body = (op.get("description") or "").strip() or (op.get("summary") or "").strip()
        # A FastAPI-synthesised summary IS the function's own title — never instructions.
        synthesised = route["path"].strip("/").replace("/", " ").replace("_", " ").title()
        if len(body) < 120 or body.title() == synthesised:
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
