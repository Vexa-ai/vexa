"""Subject tickets (subject-ticket.v1) — flows carries them from the producer's fact to the door.

Flows mints no ticket for a platform fact and verifies none: the gateway's service door does. What
flows owes the door is CARRIAGE — the ticket a producer attached to a person's fact must reach the
door call that fact causes, and only that call:

  * admission keeps `refs._tickets`; a second delivery of the same fact adds its own (union);
  * a fact a step emits inherits its parent's tickets; a keyed fire keeps the arming event's;
  * a person's flow presents the routine grant stored with its version on every run;
  * `door_headers` sends the running step's tickets — and nothing outside a step or request;
  * flows-api presents a caller's ticket and the one the door answers on validate;
  * the mail intake is an issuer: an authenticated invite or reply carries a `mail` ticket;
  * a door refusal for a missing or refused ticket is retryable, a replayed one is final.

Offline: the sqlite double, FakeClock, identity stubbed at its one hop.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from flows import Done, EventType, FakeClock, Registry, StepError, admit, keyed, schedule, tick  # noqa: E402
from flows import tickets as engine_tickets  # noqa: E402
from flows_integrations import fact_keys, mail_tickets  # noqa: E402
from flows_integrations.mailbox import handle, invite_native  # noqa: E402
from flows_steps import common  # noqa: E402
from flows_steps import subject_ticket as st  # noqa: E402
from flows_steps.emailx import register_thread  # noqa: E402
from mail_auth_fixture import authed  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "VEXA_FLOWS_SERVICE_KEY": "test-flows-service-key-not-a-placeholder",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://tickets:unreachable@127.0.0.1:1/flows"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations import flows_api as fa  # noqa: E402
    from flows_integrations import subject_auth  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

if not isinstance(fa.db, SqliteDB):
    fa.db = SqliteDB()

T1, T2, GRANT = "st1.ticket-one.sig", "st1.ticket-two.sig", "st1.grant-of-anna.sig"


def _keypair(tmp_path):
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    path = tmp_path / "mail.pem"
    path.write_bytes(pem)
    return path, fact_keys.load_check(fact_keys.public_pem(pem))


def _rig():
    """A registry whose one step records the headers its door call would send."""
    db, clock, reg = SqliteDB(), FakeClock(), Registry()
    seen: list = []

    @reg.step
    def call_door(ctx):
        seen.append(common.door_headers(ctx.refs.get("uid")))
        return Done()

    @reg.step
    def emit_child(ctx):
        ctx.emit("child.happened", f"child-{ctx.reaction.source_event_id}",
                 {"uid": ctx.refs.get("uid")})
        return Done()

    reg.flow(name="on_fact", version=1, on=EventType("fact.happened"), steps=[call_door])
    reg.flow(name="parent", version=1, on=EventType("parent.happened"), steps=[emit_child])
    reg.flow(name="child", version=1, on=EventType("child.happened"), steps=[call_door])
    return db, clock, reg, seen


def _refs(db, flow):
    return json.loads(db.execute("SELECT subject_refs FROM reaction WHERE flow = :f",
                                 {"f": flow})[0][0])


def _drain(db, reg, clock, emit=None):
    while tick(db, reg, clock, emit=emit):
        pass


# ── the engine ──────────────────────────────────────────────────────────────────────────────────
def test_a_second_delivery_adds_its_tickets_to_the_admitted_reaction():
    db, clock, reg, _ = _rig()
    admit(db, reg, clock, source_event_id="done-1", event_type="fact.happened",
          subject_refs={"uid": "7", "meeting_id": "1", "_tickets": [T1]})
    admit(db, reg, clock, source_event_id="done-1", event_type="fact.happened",
          subject_refs={"uid": "7", "meeting_id": "1", "_tickets": [T2, T1]})
    assert _refs(db, "on_fact")["_tickets"] == [T1, T2]
    admit(db, reg, clock, source_event_id="done-1", event_type="fact.happened",
          subject_refs={"uid": "7", "meeting_id": "1"})
    assert _refs(db, "on_fact")["_tickets"] == [T1, T2], "a delivery without tickets drops none"


def test_a_delivery_without_tickets_then_one_with_them_carries_them():
    db, clock, reg, _ = _rig()
    admit(db, reg, clock, source_event_id="done-2", event_type="fact.happened",
          subject_refs={"uid": "7"})
    admit(db, reg, clock, source_event_id="done-2", event_type="fact.happened",
          subject_refs={"uid": "7", "_tickets": [T1]})
    assert _refs(db, "on_fact")["_tickets"] == [T1]


def test_a_child_fact_inherits_its_parents_tickets():
    db, clock, reg, seen = _rig()

    def emit(event_type, source_id, refs):
        return admit(db, reg, clock, source_event_id=source_id, event_type=event_type,
                     subject_refs=refs)

    admit(db, reg, clock, source_event_id="p-1", event_type="parent.happened",
          subject_refs={"uid": "7", "_tickets": [T1]})
    _drain(db, reg, clock, emit=emit)
    assert _refs(db, "child")["_tickets"] == [T1]
    assert seen[-1][st.HEADER] == T1, "the child flow's door call presents the parent's ticket"


def test_a_keyed_fire_keeps_the_arming_events_tickets():
    db, clock, reg, seen = _rig()
    from flows.triggers import check_spec
    spec = check_spec({"kind": "relative", "event": "calendar.event.updated",
                       "at": "payload.start - 60s", "key": "payload.event_id"})
    reg.flow_by_names(name="bot_at_start", version=1, on_event="calendar.event.updated",
                      step_names=["call_door"], params={"_trigger": spec})
    start = clock.now() + 600
    admit(db, reg, clock, source_event_id="cal-1", event_type="calendar.event.updated",
          subject_refs={"uid": "7", "event_id": "e1", "start": start, "_tickets": [T1]})
    clock._t = start - 30
    assert keyed.fire_due(db, reg, clock) == 1
    assert _refs(db, "bot_at_start")["_tickets"] == [T1]
    _drain(db, reg, clock)
    assert seen[-1][st.HEADER] == T1


def test_a_persons_scheduled_run_presents_its_routine_grant():
    db, clock, reg, seen = _rig()
    clock._t = 1_000_000.0 - (1_000_000.0 % 3600) + 3600 + 5     # five seconds past an hour
    db.execute("INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, "
               "status, created_by, created_at, subject_grant) VALUES ('user:7', 'hourly', 1, "
               "'schedule.due', '0 * * * *', '[\"call_door\"]', '{}', 'active', 'anna', 0, :g)",
               {"g": GRANT})
    reg.refresh_from_db(db)
    assert schedule.fire_due(db, reg, clock) == 1
    _drain(db, reg, clock)
    assert seen[-1]["X-User-Id"] == "7"
    assert seen[-1][st.HEADER] == GRANT


def test_an_instance_flow_presents_no_grant_and_a_fact_without_tickets_sends_no_header():
    db, clock, reg, seen = _rig()
    admit(db, reg, clock, source_event_id="f-0", event_type="fact.happened",
          subject_refs={"uid": "7"})
    _drain(db, reg, clock)
    assert st.HEADER not in seen[-1]


def test_door_headers_sends_the_running_steps_tickets_and_none_outside_a_step():
    db, clock, reg, seen = _rig()
    admit(db, reg, clock, source_event_id="f-1", event_type="fact.happened",
          subject_refs={"uid": "7", "_tickets": [T1, T2]})
    _drain(db, reg, clock)
    assert seen[-1][st.HEADER] == f"{T1},{T2}"
    outside = common.door_headers("7")
    assert st.HEADER not in outside, "outside a step nothing is presented"
    assert engine_tickets.presented() == []
    assert common.door_headers("7", tickets=[T2])[st.HEADER] == T2, "an explicit ticket is sent"


# ── the door's refusals ─────────────────────────────────────────────────────────────────────────
def _door_answers(monkeypatch, code, body):
    def fake(req, timeout=20):
        raise urllib.error.HTTPError(req.full_url, code, "refused", {},
                                     io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(common.urllib.request, "urlopen", fake)


@pytest.mark.parametrize("reason", ["subject_ticket_required", "subject_ticket_refused"])
def test_a_missing_or_refused_ticket_is_a_retryable_step_error(monkeypatch, reason):
    _door_answers(monkeypatch, 403, {"reason": reason})
    with pytest.raises(StepError) as e:
        common.http("GET", "http://door/meetings/transcripts/by-id/1", common.door_headers("7"))
    assert e.value.retryable and reason in str(e.value)


def test_a_replayed_ticket_is_final(monkeypatch):
    _door_answers(monkeypatch, 403, {"reason": "ticket_replayed"})
    with pytest.raises(StepError) as e:
        common.http("POST", "http://door/meetings/bots", common.door_headers("7"), {})
    assert not e.value.retryable


def test_any_other_403_is_still_an_answer(monkeypatch):
    _door_answers(monkeypatch, 403, {"reason": "not_on_the_allow_list"})
    assert common.http("GET", "http://door/x", {})[0] == 403


# ── flows-api ───────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def api(monkeypatch):
    for t in ("flow_version", "effect_receipt", "reaction"):
        fa.db.execute(f"DELETE FROM {t}")
    sent: list = []

    def validate(base, token, secret):
        engine_tickets.add(["st1.door-answered.sig"])        # what `_validate` adds on a 200
        return {"anna-key": (200, {"user_id": 126, "email": "anna@vexa.test",
                                   "credential_class": "session"})}.get(token, (401, {}))

    monkeypatch.setattr(subject_auth, "_validate", validate)
    monkeypatch.setattr(subject_auth, "_request_grant",
                        lambda base, token, key, routine: f"st1.gateway-grant-{routine}.sig")
    monkeypatch.setattr(fa, "_announce", lambda *a, **k: sent.append(common.door_headers()) or True)
    import importlib
    pol = importlib.import_module("flows_steps.policies")
    monkeypatch.setattr(pol, "read", lambda uid: pol.resolve(None))
    return TestClient(fa.app, raise_server_exceptions=False), sent


def test_validate_records_the_ticket_the_door_answers(monkeypatch):
    def fake_http(method, url, headers, body=None, timeout=20, response_headers=None):
        response_headers.update({st.HEADER.lower(): "st1.from-door.sig"})
        return 200, {"user_id": 5}

    monkeypatch.setattr(common, "http", fake_http)
    with engine_tickets.presenting([]):
        subject_auth._validate("http://door/identity", "tok", "key")
        assert engine_tickets.presented() == ["st1.from-door.sig"]


def test_a_request_presents_the_callers_ticket_and_the_doors(api, monkeypatch):
    client, _ = api
    seen: list = []
    monkeypatch.setattr(fa, "_routine_facts",
                        lambda scope, name: seen.append(common.door_headers("126")) or {})
    r = client.post("/routines/triggers", headers={"Authorization": "Bearer anna-key",
                                                   st.HEADER: "st1.from-agent.sig"},
                    json={"thread": "t-1", "name": "daily", "instruction": "say hi",
                          "trigger": {"kind": "schedule", "cron": "0 9 * * *"}})
    assert r.status_code == 201, r.text
    presented = seen[-1][st.HEADER].split(",")
    assert "st1.from-agent.sig" in presented and "st1.door-answered.sig" in presented
    assert engine_tickets.presented() == [], "nothing leaks out of the request"


def test_a_session_arming_its_routine_stores_the_doors_grant(api):
    client, _ = api
    r = client.post("/flows", headers={"Authorization": "Bearer anna-key"},
                    json={"name": "morning", "trigger": {"kind": "schedule", "cron": "0 8 * * *"},
                          "steps": ["agent_run"], "params": {"instruction": "brief me"}})
    assert r.status_code == 201, r.text
    g = fa.db.execute("SELECT subject_grant FROM flow_version WHERE name = 'morning'")[0][0]
    assert g == "st1.gateway-grant-user:126/morning.sig"


def test_the_operator_activating_for_a_person_stores_the_grant_it_presents(api):
    client, _ = api
    op = {"X-Flows-Operator-Key": "test-flows-key"}
    r = client.post("/flows", headers=op,
                    json={"name": "evening", "scope": "user:126", "activate": False,
                          "trigger": {"kind": "schedule", "cron": "0 18 * * *"},
                          "steps": ["agent_run"], "params": {"instruction": "wrap up"}})
    assert r.status_code == 201, r.text
    assert fa.db.execute("SELECT subject_grant FROM flow_version WHERE name = 'evening'")[0][0] is None
    r = client.post("/flows/evening/1/resume?scope=user:126", headers=op,
                    json={"grant": "st1.agent-grant.sig"})
    assert r.status_code == 200, r.text
    assert (fa.db.execute("SELECT subject_grant FROM flow_version WHERE name = 'evening'")[0][0]
            == "st1.agent-grant.sig")
    r = client.post("/flows/evening/1/resume?scope=user:126",
                    headers={**op, "X-Vexa-Subject-Grant": "st1.header-grant.sig"})
    assert r.status_code == 200, r.text
    assert (fa.db.execute("SELECT subject_grant FROM flow_version WHERE name = 'evening'")[0][0]
            == "st1.header-grant.sig")
    bad = client.post("/flows/evening/1/resume?scope=user:126", headers=op,
                      json={"grant": "not-a-ticket"})
    assert bad.status_code == 400 and bad.json()["detail"]["refused"] == "bad_grant"


def test_attaching_a_grant_keeps_the_routines_status(api):
    """agent-api re-issuing the grant of a routine it recorded a person turning on: the grant is
    stored, and a paused routine stays paused (attaching is not arming)."""
    client, _ = api
    op = {"X-Flows-Operator-Key": "test-flows-key"}
    client.post("/flows", headers=op,
                json={"name": "late", "scope": "user:126", "activate": False,
                      "trigger": {"kind": "schedule", "cron": "0 22 * * *"},
                      "steps": ["agent_run"], "params": {"instruction": "x"}})
    before = fa.db.execute("SELECT status FROM flow_version WHERE name = 'late'")[0][0]
    r = client.post("/flows/late/1/grant?scope=user:126", headers=op,
                    json={"grant": "st1.adopted.sig"})
    assert r.status_code == 200, r.text
    row = fa.db.execute("SELECT status, subject_grant FROM flow_version WHERE name = 'late'")[0]
    assert row[0] == before and row[1] == "st1.adopted.sig"
    assert client.post("/flows/late/1/grant?scope=user:126", headers=op,
                       json={}).status_code == 400
    assert client.post("/flows/late/9/grant?scope=user:126", headers=op,
                       json={"grant": "st1.x.y"}).status_code == 404


def test_a_submission_cannot_plant_a_grant_through_params(api):
    client, _ = api
    r = client.post("/flows", headers={"X-Flows-Operator-Key": "test-flows-key"},
                    json={"name": "noon", "scope": "user:126",
                          "trigger": {"kind": "schedule", "cron": "0 12 * * *"},
                          "steps": ["agent_run"],
                          "params": {"instruction": "x", "subject_grant": "st1.planted.sig"}})
    assert r.status_code == 201, r.text
    assert fa.db.execute("SELECT subject_grant FROM flow_version WHERE name = 'noon'")[0][0] is None


def test_a_producers_header_ticket_rides_the_fact_it_posts(api):
    client, _ = api
    fa.db.execute("INSERT INTO flow_version (scope, name, version, on_event, steps, params, status, "
                  "created_by, created_at) VALUES ('instance', 'on_fact_posted', 1, 'fact.posted', "
                  "'[\"agent_run\"]', '{}', 'active', 'op', 0)")
    r = client.post("/events", headers={"X-Flows-Operator-Key": "test-flows-key",
                                        st.HEADER: "st1.header.sig"},
                    json={"event_type": "fact.posted", "source_event_id": "fp-1",
                          "refs": {"uid": "126", "_tickets": ["st1.refs.sig"]}})
    assert r.status_code == 202, r.text
    refs = json.loads(fa.db.execute("SELECT subject_refs FROM reaction WHERE source_event_id "
                                    "LIKE 'fp-1%'")[0][0])
    assert refs["_tickets"] == ["st1.refs.sig", "st1.header.sig"]


# ── the mail issuer ─────────────────────────────────────────────────────────────────────────────
SELF = "vexa@acme.test"
ICS = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:u-42\nDTSTART:20300302T140000Z\n"
       "ORGANIZER:mailto:boss@acme.test\nATTENDEE:mailto:ben@acme.test\n"
       "ATTENDEE:mailto:eve@partner.test\nSUMMARY:Pilot sync\n"
       "LOCATION:https://meet.google.com/abc-defg-hij\nEND:VEVENT\nEND:VCALENDAR\n")


def _mail_rig():
    db, clock, reg = SqliteDB(), FakeClock(), Registry()

    @reg.step
    def noop(ctx):
        return Done()

    reg.flow(name="invite_intake", version=1, on=EventType("invite.received"), steps=[noop])
    reg.flow(name="email_chat", version=1, on=EventType("mail.reply"), steps=[noop])
    return db, clock, reg


def _msg(frm, *, ics=None, headers=None):
    return SimpleNamespace(frm=frm, body="hello", subject="hi", headers=authed(frm, headers),
                           ics=ics, message_id=f"<{frm}-1@x.test>", ext_id="ext-1", cursor="c1")


def _ticket_of(db, event_type, check, clock):
    refs = json.loads(db.execute("SELECT subject_refs FROM reaction WHERE event_type = :e",
                                 {"e": event_type})[0][0])
    (token,) = refs["_tickets"]
    return st.read(token, {"mail": check}, now=int(clock.now()))


def test_an_authenticated_invite_carries_a_mail_ticket(tmp_path, monkeypatch):
    path, check = _keypair(tmp_path)
    monkeypatch.setenv(mail_tickets.KEY_FILE_ENV, str(path))
    db, clock, reg = _mail_rig()
    out = handle(db, reg, clock, SELF, _msg("boss@acme.test", ics=ICS),
                 lambda e: {"boss@acme.test": "7"}.get(e), lambda u: True, None)
    assert out == ("invite", 1)
    c = _ticket_of(db, "invite.received", check, clock)
    assert (c["iss"], c["kind"], c["sub"], c["addr"]) == ("mail", "event", "7", "boss@acme.test")
    assert c["native"] == "google_meet/abc-defg-hij"
    assert c["peers"] == ["ben@acme.test", "eve@partner.test"]
    assert c["once"] == ["bot.dispatch"]
    assert set(c["cls"]) == {"bot.dispatch", "transcript.read", "desk.write", "chat.autonomous"}
    assert c["exp"] - c["iat"] == st.MAX_LIFETIME[("mail", "event")], "capped: the meeting is far off"
    assert c["evt"] == "ics-u-42-20300302T140000Z"


def test_an_invite_from_an_organizer_who_is_not_a_user_names_the_address_alone(tmp_path,
                                                                               monkeypatch):
    path, check = _keypair(tmp_path)
    monkeypatch.setenv(mail_tickets.KEY_FILE_ENV, str(path))
    db, clock, reg = _mail_rig()
    handle(db, reg, clock, SELF, _msg("boss@acme.test", ics=ICS), lambda e: None,
           lambda u: True, None)
    c = _ticket_of(db, "invite.received", check, clock)
    assert (c["sub"], c["addr"]) == ("", "boss@acme.test")


def test_a_reply_on_a_persons_thread_carries_a_mail_ticket(tmp_path, monkeypatch):
    path, check = _keypair(tmp_path)
    monkeypatch.setenv(mail_tickets.KEY_FILE_ENV, str(path))
    db, clock, reg = _mail_rig()
    register_thread(db, "<t1@vexa.ai>", "7", "meet-97")
    out = handle(db, reg, clock, SELF, _msg("anna@acme.test", headers={"In-Reply-To": "<t1@vexa.ai>"}),
                 lambda e: {"anna@acme.test": "7"}.get(e), lambda u: True, None)
    assert out == ("thread_reply", 1)
    c = _ticket_of(db, "mail.reply", check, clock)
    assert (c["sub"], c["addr"], c["exp"] - c["iat"]) == ("7", "anna@acme.test", 86400)
    assert set(c["cls"]) == {"chat.autonomous", "desk.write"} and "once" not in c


def test_no_key_no_ticket_and_the_intake_still_admits(monkeypatch):
    monkeypatch.delenv(mail_tickets.KEY_FILE_ENV, raising=False)
    db, clock, reg = _mail_rig()
    out = handle(db, reg, clock, SELF, _msg("boss@acme.test", ics=ICS), lambda e: "7",
                 lambda u: True, None)
    assert out == ("invite", 1)
    refs = json.loads(db.execute("SELECT subject_refs FROM reaction")[0][0])
    assert "_tickets" not in refs


def test_an_unreadable_key_is_no_ticket_never_a_crash(tmp_path, monkeypatch):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a key")
    monkeypatch.setenv(mail_tickets.KEY_FILE_ENV, str(bad))
    db, clock, reg = _mail_rig()
    assert handle(db, reg, clock, SELF, _msg("boss@acme.test", ics=ICS), lambda e: "7",
                  lambda u: True, None) == ("invite", 1)
    assert "_tickets" not in json.loads(db.execute("SELECT subject_refs FROM reaction")[0][0])


@pytest.mark.parametrize("url,expected", [
    ("https://meet.google.com/abc-defg-hij", ("google_meet", "abc-defg-hij")),
    ("https://us02web.zoom.us/j/81234567890?pwd=abc", ("zoom", "81234567890")),
    ("https://teams.microsoft.com/meet/123456789012?p=x", ("teams", "123456789012")),
    ("https://meet.jit.si/MyRoom", ("jitsi", "MyRoom")),
    ("https://example.com/nothing", ("", "")),
])
def test_invite_native_names_the_meeting_as_meeting_api_files_it(url, expected):
    assert invite_native(url) == expected
