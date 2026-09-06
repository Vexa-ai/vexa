"""THE FRICTION SINK TELLS THE TRUTH AND STOPS PUTTING PEOPLE'S WORDS IN URLS — review B5/B6/B7.

`tests/test_friction.py` owns what the sink CATCHES: nothing a caller sends or omits can cost them
the report. That contract is untouched here and these tests are additive to it. This file is about
three things that were wrong ABOUT the catch rather than in it:

  B7  `POST /friction` took every field as a QUERY PARAMETER — up to 900 characters of a person's
      own words about a failure, in the URL, where every proxy and ingress access log in front of
      this service writes them down. That flows-api does not log them itself is a property of ONE
      line (`uvicorn(… log_level="warning")`), not of the design. The route now takes a JSON body.
      The query spelling is KEPT FOR ONE RELEASE, deprecated and warned once per process, because
      the deployed MCP edge sends these nine names as query arguments (`vexa_mcp/register.py` puts
      every declared argument in `params=`) — a sink that starts refusing the transport its own
      shipped edge uses is F-D26 with a new cause.

  B6  the route answered `recorded: true` UNCONDITIONALLY, discarding `admit()`'s return. A
      deployment whose `friction_log` flow is retired, or whose registry never loaded it, told
      every reporter their report was recorded while nothing was created and the row did not
      exist. A sink that lies about catching is worse than one that refuses: the reporter stops
      filing and nobody learns anything.

  B5  the SERVED manifest said `friction.reported` triggers "no flow", which is the exact failure
      B6 is about, asserted as if it were the design.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest
from sqlite_double import SqliteDB

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-friction-body",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+psycopg://friction-body:unreachable@127.0.0.1:1/flows"}


@pytest.fixture(scope="module")
def api():
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
    return flows_api, TestClient(flows_api.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def clean(api):
    flows_api, _ = api
    flows_api.db.execute("DELETE FROM reaction")
    yield
    flows_api.db.execute("DELETE FROM reaction")


def _headers(flows_api, uid="126"):
    return {"X-Flows-Operator-Key": flows_api.API_KEY, "X-User-Id": uid}


def _refs(flows_api):
    rows = flows_api.db.execute(
        "SELECT subject_refs FROM reaction WHERE event_type = 'friction.reported'")
    assert len(rows) == 1, f"expected exactly one filed report, found {len(rows)}"
    return json.loads(rows[0][0])


# ── B7 · the report travels in a body ───────────────────────────────────────────────────────────

def test_a_report_sent_as_a_json_body_is_filed(api):
    flows_api, client = api
    r = client.post("/friction", headers=_headers(flows_api), json={
        "session": "chat-77", "what_i_tried": "asked for the transcript",
        "what_happened": "the link 404'd", "severity": "blocker", "kind": "no-page"})
    assert r.status_code == 201, r.text
    assert r.json()["recorded"] is True
    refs = _refs(flows_api)
    assert refs["what_i_tried"] == "asked for the transcript"
    assert refs["what_happened"] == "the link 404'd"
    assert refs["session"] == "chat-77" and refs["severity"] == "blocker"
    assert refs["kind"] == "no-page"


def test_the_words_never_have_to_touch_the_url(api):
    """The whole point of B7, stated as the property a proxy log would show: a body-only call
    carries a 900-character report and the request line carries none of it."""
    flows_api, client = api
    words = "the bot said it joined and it did not " * 20
    r = client.post("/friction", headers=_headers(flows_api),
                    json={"what_happened": words, "session": "s-1"})
    assert r.status_code == 201, r.text
    assert str(r.request.url).endswith("/friction"), (
        f"the report leaked into the request line: {r.request.url}")
    assert _refs(flows_api)["what_happened"].startswith("the bot said it joined")


def test_fields_the_route_does_not_name_are_kept_from_the_body_too(api):
    """"Rich data, does not have to be too structured" (founder, 2026-09-04) does not become a
    property of one transport: an extra field is kept whichever way it arrived."""
    flows_api, client = api
    r = client.post("/friction", headers=_headers(flows_api),
                    json={"what_happened": "x", "request_id": "req-9", "model": "sonnet"})
    assert r.status_code == 201, r.text
    assert _refs(flows_api)["extra"] == {"model": "sonnet", "request_id": "req-9"}
    assert r.json()["extra"] == ["model", "request_id"]


def test_an_extra_body_field_cannot_re_address_the_report(api):
    """The namespacing rule the route documents, checked on the new transport: `concerns()` reads
    `uid`/`owner`/`organizer` straight off the refs, so a caller-supplied key merged at the top
    level would be authority rather than data."""
    flows_api, client = api
    r = client.post("/friction", headers=_headers(flows_api),
                    json={"what_happened": "x", "uid": "512", "organizer": "ben@vexa.test"})
    assert r.status_code == 201, r.text
    refs = _refs(flows_api)
    assert refs["uid"] == "126", "a body field re-addressed the report"
    assert refs.get("organizer") is None
    assert refs["extra"]["uid"] == "512" and refs["extra"]["organizer"] == "ben@vexa.test"


def test_the_query_spelling_still_works_for_one_release(api):
    """The deployed MCP manifest sends these names in `params=`. Breaking it would throw away
    reports to fix a logging problem, which is the F-D26 trade run backwards."""
    flows_api, client = api
    r = client.post("/friction", headers=_headers(flows_api),
                    params={"what_i_tried": "a", "what_happened": "b", "session": "s-q"})
    assert r.status_code == 201, r.text
    refs = _refs(flows_api)
    assert refs["what_i_tried"] == "a" and refs["what_happened"] == "b"
    assert refs["session"] == "s-q"


def test_the_query_spelling_says_once_that_it_is_deprecated(api, capsys):
    """Once per process, like the `X-Flows-Admin-Key` deprecation and for the same reason: printed
    per request it is a flood on the hot path that nobody reads."""
    flows_api, client = api
    flows_api._FRICTION_QUERY_SAID.clear()
    client.post("/friction", headers=_headers(flows_api), params={"what_happened": "b"})
    said = capsys.readouterr().out
    assert "DEPRECATED" in said and "JSON body" in said
    client.post("/friction", headers=_headers(flows_api), params={"what_happened": "c"})
    assert capsys.readouterr().out == "", "once per process, not once per request"


def test_a_body_report_says_nothing_about_deprecation(api, capsys):
    flows_api, client = api
    flows_api._FRICTION_QUERY_SAID.clear()
    client.post("/friction", headers=_headers(flows_api), json={"what_happened": "b"})
    assert capsys.readouterr().out == ""


def test_the_body_wins_when_a_field_arrives_twice(api):
    """One rule, stated: a caller mid-migration sends both, and answering on the old one would make
    the migration invisible to them — the same reasoning `_operator_key` already carries."""
    flows_api, client = api
    r = client.post("/friction", headers=_headers(flows_api),
                    params={"what_happened": "from the query"},
                    json={"what_happened": "from the body"})
    assert r.status_code == 201, r.text
    assert _refs(flows_api)["what_happened"] == "from the body"


def test_no_value_in_a_body_produces_a_400_either(api):
    """F-D27's rule is about the ROUTE, not about one transport. Absent, blank, over-long,
    unknown, non-ascii — every one is filed, sent as a body exactly as sent as a query."""
    flows_api, client = api
    ugly = ("", " ", "a" * 5000, "🤷", "../../etc/passwd", "'; DROP TABLE reaction; --", "1")
    for field in ("session", "what_i_tried", "what_happened", "severity", "kind", "meeting_id",
                  "tool", "deployment", "worker_image"):
        for word in ugly:
            flows_api.db.execute("DELETE FROM reaction")
            r = client.post("/friction", headers=_headers(flows_api), json={field: word})
            assert r.status_code == 201, f"{field}={word!r} produced {r.status_code}: {r.text[:120]}"


def test_a_body_that_is_not_an_object_does_not_cost_the_report(api):
    """A malformed body is the reporter's tooling, not the reporter. The route falls back to the
    query fields rather than refusing — the only refusal left here is authentication."""
    flows_api, client = api
    r = client.post("/friction",
                    headers={**_headers(flows_api), "Content-Type": "application/json"},
                    params={"what_happened": "still filed"},
                    content=b"not json at all")
    assert r.status_code == 201, r.text
    assert _refs(flows_api)["what_happened"] == "still filed"


# ── B6 · `recorded` is the admission's answer, not the route's intention ────────────────────────

def test_recorded_is_false_when_the_admission_created_nothing(api, monkeypatch, caplog):
    """A deployment whose `friction_log` flow is retired (or whose registry never loaded it) admits
    the fact into no flow: `admit()` returns 0, `flows/admission.py` creates no row, and the report
    is not readable back. It used to answer `recorded: true` anyway."""
    import logging

    flows_api, client = api
    monkeypatch.setattr(flows_api.vocab, "match", lambda _event: [])
    monkeypatch.setattr(flows_api.vocab, "refresh_from_db", lambda _db: 0)
    with caplog.at_level(logging.ERROR):
        r = client.post("/friction", headers=_headers(flows_api),
                        json={"what_happened": "nothing catches this"})
    assert r.status_code == 201, r.text
    assert r.json()["recorded"] is False, "the sink claimed a catch it did not make"
    assert "note" in r.json()
    assert flows_api.db.execute(
        "SELECT COUNT(*) FROM reaction WHERE event_type='friction.reported'")[0][0] == 0
    assert any("NOT recorded" in rec.getMessage() for rec in caplog.records), (
        "nothing was logged about a report that was not stored")


def test_recorded_is_true_when_a_row_really_exists(api):
    """The control: the same call, on a deployment whose registry does carry `friction_log`, is
    recorded AND readable back off the reaction row with no worker tick."""
    flows_api, client = api
    r = client.post("/friction", headers=_headers(flows_api),
                    json={"what_happened": "the bot never joined"})
    assert r.status_code == 201 and r.json()["recorded"] is True, r.text
    back = client.get("/friction", headers=_headers(flows_api)).json()
    assert back["count"] == 1 and back["reports"][0]["id"] == r.json()["id"]


# ── B5 · the manifest says which flow the event triggers ────────────────────────────────────────

def _manifest() -> dict:
    path = pathlib.Path(__file__).resolve().parents[1] / "mcp.tools.v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_manifest_names_the_flow_friction_reported_actually_triggers():
    """It said "no flow — recorded as a fact on the timeline only". `flows_defs/production.py`
    registers `friction_log@1` on this event, and without it `admit()` creates nothing — so the
    manifest was publishing the B6 failure as if it were the design."""
    published = {e["event"]: e for e in _manifest()["publishes_events"]}
    assert "friction_log" in published["friction.reported"]["triggers"]
    assert "no flow" not in published["friction.reported"]["triggers"]


def test_the_manifest_and_the_registry_agree_about_that_flow():
    """The pair that makes the line above a fact rather than a nicer string: what the manifest
    claims is what `production.build` registers, checked in the profile with no agent domain."""
    from flows import Registry
    from flows_defs import production

    reg = Registry()
    production.build(reg, SqliteDB())
    names = {f.name for f in reg.match("friction.reported")}
    published = {e["event"]: e for e in _manifest()["publishes_events"]}
    for name in names:
        assert name in published["friction.reported"]["triggers"], (
            f"the registry reacts with {name!r} and the manifest does not say so")


def test_the_manifest_is_still_valid_json_with_the_nine_arguments_the_edge_sends():
    """The deployed edge builds its call from `arguments`; B7 keeps every one of those names."""
    tools = {t["name"]: t for t in _manifest()["tools"]}
    assert tools["report_friction"]["arguments"] == [
        "session", "what_i_tried", "what_happened", "severity", "meeting_id", "tool",
        "deployment", "worker_image", "kind"]
