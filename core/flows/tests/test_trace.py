"""THE TRACE THROUGH FLOWS — logevent.v1 on flows-api, flows-worker and the mailbox (Vexa-ai/vexa#1783).

Three processes, three ways a unit of work starts, one rule: every line the unit emits and every
call it makes to a Vexa service carry the SAME `trace_id`.

  * flows-api adopts the caller's `X-Trace-Id` (mints one if absent), echoes it, and forwards it on
    the identity hop. When identity's answer carries `delegation.unit` — verified inside the
    delegation token's signature — that unit is the line's `session`; a header never is.
  * the worker mints one per `tick` (one step); the mailbox one per message.

OFFLINE: the one HTTP door (`urllib.request.urlopen` as `flows_steps.common.http` reaches it) is
replaced by a recorder, so what is under test is the header the real code builds.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

# The same literals test_subject_bearer.py imports flows_api with: the module captures them as
# constants at import, and whichever test module imports first wins.
_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://subject-bearer:unreachable@127.0.0.1:1/flows"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations import flows_api as fa  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

import flows_obs  # noqa: E402
from flows import Done, EventType, FakeClock, Registry, admit  # noqa: E402
from flows_steps import common  # noqa: E402
from flows_integrations import mailbox  # noqa: E402
from flows_worker.unit import instrument, run_unit  # noqa: E402

SCHEMA = json.loads((Path(__file__).resolve().parents[3]
                     / "core/gateway/contracts/logevent.v1/logevent.schema.json").read_text())
LOGEVENT = SCHEMA["$defs"]["LogEvent"]
TRACE = "0123456789abcdef0123456789abcdef"
UNIT = "agent-126-chat-t42"
SECRET_TOKEN = "vxd_super-secret-delegation-token"


def conforms(line: dict) -> None:
    """logevent.v1 `LogEvent`, checked without a jsonschema dependency: required keys, no extras,
    enums, and the per-field types the schema states."""
    props = LOGEVENT["properties"]
    assert set(LOGEVENT["required"]) <= set(line), line
    assert set(line) <= set(props), f"keys outside logevent.v1: {set(line) - set(props)}"
    assert line["level"] in SCHEMA["$defs"]["Level"]["enum"]
    assert line["audience"] in SCHEMA["$defs"]["Audience"]["enum"]
    for k in ("ts", "service", "trace_id", "event", "span", "meeting_id", "session"):
        if k in line:
            assert isinstance(line[k], str) and line[k], (k, line)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", line["ts"])
    if "user_id" in line:
        assert isinstance(line["user_id"], (str, int))
    if "fields" in line:
        assert isinstance(line["fields"], dict)
    assert re.fullmatch(r"[a-z][a-z0-9_]*", line["event"])


@pytest.fixture
def lines():
    sink: list = []
    flows_obs.capture(sink)
    yield sink
    flows_obs.capture(None)


class _Resp(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def wire(monkeypatch):
    """The outbound door, recorded. `answers` maps a url substring to (status, body)."""
    calls: list = []
    answers: dict = {}

    def urlopen(req, timeout=None):
        calls.append({"url": req.full_url, "trace": req.get_header("X-trace-id"),
                      "headers": dict(req.header_items())})
        for frag, (st, body) in answers.items():
            if frag in req.full_url:
                return _Resp(st, body)
        return _Resp(200, {})

    monkeypatch.setattr(common.urllib.request, "urlopen", urlopen)
    return SimpleNamespace(calls=calls, answers=answers)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(fa, "db", SqliteDB())
    return TestClient(fa.app, raise_server_exceptions=False)


def _identity(wire, delegation=None):
    body = {"user_id": 126, "email": "anna@vexa.test"}
    if delegation is not None:
        body["delegation"] = delegation
    wire.answers["/internal/validate"] = (200, body)


# ── flows-api ─────────────────────────────────────────────────────────────────────────────────
def test_inbound_trace_is_on_every_line_echoed_and_forwarded_to_identity(client, wire, lines):
    _identity(wire)
    r = client.get("/reactions", headers={"Authorization": "Bearer anna-key", "X-Trace-Id": TRACE})
    assert r.status_code == 200, r.text
    assert r.headers["x-trace-id"] == TRACE
    api = [ln for ln in lines if ln["service"] == "flows-api"]
    assert {ln["event"] for ln in api} >= {"request_received", "caller_resolved"}
    assert {ln["trace_id"] for ln in api} == {TRACE}
    validate = [c for c in wire.calls if "/internal/validate" in c["url"]]
    assert validate and all(c["trace"] == TRACE for c in validate), wire.calls
    for ln in lines:
        conforms(ln)


def test_a_request_without_a_trace_gets_one_minted_and_echoed(client, wire, lines):
    _identity(wire)
    r = client.get("/reactions", headers={"Authorization": "Bearer anna-key"})
    minted = r.headers["x-trace-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", minted)
    assert {ln["trace_id"] for ln in lines if ln["service"] == "flows-api"} == {minted}
    assert [c["trace"] for c in wire.calls] == [minted]


def test_delegation_unit_from_identity_becomes_the_session(client, wire, lines):
    _identity(wire, {"regime": "human", "workspaces": [], "target": None, "unit": UNIT})
    r = client.get("/reactions", headers={"Authorization": f"Bearer {SECRET_TOKEN}",
                                          "X-Trace-Id": TRACE})
    assert r.status_code == 200, r.text
    resolved = [ln for ln in lines if ln["event"] == "caller_resolved"]
    assert resolved and all(ln["session"] == UNIT for ln in resolved)
    for ln in lines:
        conforms(ln)


def test_a_client_header_never_sets_the_session(client, wire, lines):
    _identity(wire)        # a person's own key: no delegation, so no session
    r = client.get("/reactions", headers={"Authorization": "Bearer anna-key", "X-Trace-Id": TRACE,
                                          "X-User-Delegation-Unit": "agent-forged",
                                          "X-Vexa-Session": "agent-forged"})
    assert r.status_code == 200
    assert lines and not any("session" in ln for ln in lines), lines
    assert "agent-forged" not in json.dumps(lines)


def test_lines_carry_no_credential_and_no_address(client, wire, lines):
    _identity(wire, {"regime": "human", "unit": UNIT})
    client.get("/reactions", headers={"Authorization": f"Bearer {SECRET_TOKEN}"})
    dumped = json.dumps(lines)
    for secret in (SECRET_TOKEN, "test-internal-secret", "anna@vexa.test"):
        assert secret not in dumped


def test_an_unusable_inbound_trace_is_replaced_not_logged(client, wire, lines):
    _identity(wire)
    r = client.get("/reactions", headers={"Authorization": "Bearer anna-key",
                                          "X-Trace-Id": "x" * 500})
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["x-trace-id"])


# ── flows-worker ──────────────────────────────────────────────────────────────────────────────
def test_one_worker_unit_shares_one_trace_across_lines_and_outbound_calls(wire, lines):
    db, clock, reg = SqliteDB(), FakeClock(1_000_000.0), Registry()

    @reg.step
    def two_calls(ctx):
        common.http("GET", "http://meeting-api.test/meetings", {"X-API-Key": "user-key-secret"})
        common.http("POST", "http://agent-api.test/api/chat", {"X-Internal-Secret": "s"}, {"m": 1})
        return Done({"ok": True})

    reg.flow(name="traced", version=1, on=EventType("thing.happened"), steps=[two_calls])
    assert admit(db, reg, clock, source_event_id="e-1", event_type="thing.happened",
                 subject_refs={"uid": "126", "meeting_id": 104, "from_addr": "anna@vexa.test"}) == 1
    instrument(reg)
    instrument(reg)        # idempotent: one wrapper, not two
    assert run_unit(db, reg, clock) is True

    worker = [ln for ln in lines if ln["service"] == "flows-worker"]
    assert [ln["event"] for ln in worker] == ["step_dispatched", "step_outcome"]
    traces = {ln["trace_id"] for ln in worker}
    assert len(traces) == 1
    (trace,) = traces
    assert [c["trace"] for c in wire.calls] == [trace, trace]
    assert worker[0]["fields"]["reaction_id"] and worker[0]["fields"]["step"] == "two_calls"
    assert worker[1]["fields"]["outcome"] == "Done"
    assert worker[0]["user_id"] == "126" and worker[0]["meeting_id"] == "104"
    dumped = json.dumps(lines)
    assert "anna@vexa.test" not in dumped and "user-key-secret" not in dumped
    for ln in lines:
        conforms(ln)


def test_two_units_get_two_traces_and_a_failure_names_only_its_class(wire, lines):
    db, clock, reg = SqliteDB(), FakeClock(1_000_000.0), Registry()

    @reg.step
    def boom(ctx):
        common.http("GET", "http://meeting-api.test/x", {})
        raise RuntimeError("leaked anna@vexa.test")

    reg.flow(name="boomer", version=1, on=EventType("b"), steps=[boom])
    admit(db, reg, clock, source_event_id="e-1", event_type="b", subject_refs={})
    admit(db, reg, clock, source_event_id="e-2", event_type="b", subject_refs={})
    instrument(reg)
    run_unit(db, reg, clock)
    run_unit(db, reg, clock)
    failed = [ln for ln in lines if ln["event"] == "step_failed"]
    assert len(failed) == 2 and failed[0]["fields"]["error"] == "RuntimeError"
    assert failed[0]["trace_id"] != failed[1]["trace_id"]
    assert [c["trace"] for c in wire.calls] == [failed[0]["trace_id"], failed[1]["trace_id"]]
    assert "anna@vexa.test" not in json.dumps(lines)
    for ln in lines:
        conforms(ln)


def test_outside_any_unit_no_trace_header_is_invented(wire):
    common.http("GET", "http://meeting-api.test/x", {})
    assert wire.calls[0]["trace"] is None


def test_third_party_wires_do_not_go_through_the_traced_door():
    """Graph and the inbox open their own connections; only `common.http` stamps X-Trace-Id."""
    src = Path(__file__).resolve().parents[1] / "src" / "flows_integrations"
    for name in ("graph_client.py", "inbox.py"):
        assert "flows_obs" not in (src / name).read_text()


# ── mailbox ───────────────────────────────────────────────────────────────────────────────────
def _mail_rig():
    db, clock, reg = SqliteDB(), FakeClock(), Registry()

    @reg.step
    def noop(ctx):
        return Done()

    reg.flow(name="email_chat", version=1, on=EventType("mail.reply"), steps=[noop])
    reg.flow(name="invite_intake", version=1, on=EventType("invite.received"), steps=[noop])
    return db, clock, reg


def _mail(frm="colleague@acme.test", ext="imap-uid-77"):
    return SimpleNamespace(frm=frm, body="the body text, private", subject="Private subject",
                           headers={}, ics=None, message_id=f"<{ext}@acme.test>", ext_id=ext,
                           cursor="c1")


def test_one_mail_is_one_trace_shared_with_its_vexa_lookups(wire, lines):
    db, clock, reg = _mail_rig()
    wire.answers["/admin/users/email/"] = (200, {"id": 126})

    out = mailbox.process(db, reg, clock, "vexa@acme.test", _mail(),
                          lambda e: common.platform_user_id(e) or None, lambda u: True,
                          lambda e: "99")
    assert out == ("known_user_mail", 1)
    (done,) = [ln for ln in lines if ln["event"] == "mail_processed"]
    assert done["service"] == "mailbox"
    assert done["fields"] == {"ext_id": "imap-uid-77", "outcome": "known_user_mail", "admitted": 1}
    assert wire.calls and all(c["trace"] == done["trace_id"] for c in wire.calls)
    dumped = json.dumps(lines)
    for private in ("colleague@acme.test", "Private subject", "the body text"):
        assert private not in dumped
    for ln in lines:
        conforms(ln)


def test_two_mails_two_traces_and_an_ignored_mail_still_logs_its_outcome(wire, lines):
    db, clock, reg = _mail_rig()
    known = lambda e: None  # noqa: E731
    mailbox.process(db, reg, clock, "vexa@acme.test", _mail("vexa@acme.test", "a"), known,
                    lambda u: False, lambda e: "99")
    mailbox.process(db, reg, clock, "vexa@acme.test", _mail("x@evil.test", "b"), known,
                    lambda u: False, lambda e: "99")
    done = [ln for ln in lines if ln["event"] == "mail_processed"]
    assert len(done) == 2 and done[0]["trace_id"] != done[1]["trace_id"]
    assert "evil.test" not in json.dumps(lines)
    for ln in lines:
        conforms(ln)
