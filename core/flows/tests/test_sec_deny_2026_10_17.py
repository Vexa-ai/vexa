"""DENY TESTS from the 2026-10-17 private security review (security-validation-2026-10-17).

Each test is named after the protection it asserts and runs against current code. A test that
passes means the protection exists. A test marked `xfail(strict=True)` names the finding whose
protection is missing today; when the protection lands, the xfail turns into an XPASS failure and
the mark comes off.

  H6 — inbound mail is placed by its `From:` alone. A known user's address becomes an agent turn in
       that user's own session, with no sender-authentication verdict (DMARC / DKIM / SPF, as a
       receiving MTA records it in `Authentication-Results`) consulted anywhere in the intake.
  H8 — a worker acting for a person in the `human` regime (the person's chat is open, but the
       agent, not the person, makes the call) creates flows that are ACTIVE by default, and may
       activate a draft, with no separate approval by the person.

OFFLINE: sqlite double, FakeClock, the identity hop stubbed at `subject_auth._validate`, agent-api
stubbed at `flows_steps.agent.http`. No containers, no network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi.testclient import TestClient  # noqa: E402
from flows import Done, EventType, FakeClock, Registry  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402
from flows_integrations.mailbox import handle  # noqa: E402
from flows_steps import agent as ag  # noqa: E402

# Same literals as test_subject_bearer.py / test_health.py: `flows_api` captures them at import and
# whichever module imports it first wins, so they must agree.
_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://subject-bearer:unreachable@127.0.0.1:1/flows"}
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

fa.db = SqliteDB()

H6 = "H6: inbound mail admitted on From: alone, no DMARC verdict checked (security-validation-2026-10-17)"
H8 = ("H8: an agent turn creates/activates a flow with no separate person approval "
      "(security-validation-2026-10-17)")


# ── H6 · inbound mail is not a person until the domain vouches for it ───────────────────────────

SELF = "vexa@acme.test"
KNOWN = "anna@acme.test"
AR_PASS = ("mx.acme.test; dkim=pass header.d=acme.test; spf=pass smtp.mailfrom=acme.test; "
           "dmarc=pass (p=REJECT) header.from=acme.test")
AR_FAIL = ("mx.acme.test; dkim=none; spf=fail smtp.mailfrom=elsewhere.test; "
           "dmarc=fail (p=REJECT) header.from=acme.test")


def _mail(frm, headers):
    return SimpleNamespace(frm=frm, body="please change my settings", subject="hi",
                           headers=headers, ics=None, message_id=f"<{frm}-1@x.test>",
                           ext_id="ext-1", cursor="c1")


def _mail_rig():
    db, clock, reg = SqliteDB(), FakeClock(), Registry()

    @reg.step
    def noop(ctx):
        return Done()

    reg.flow(name="email_chat", version=1, on=EventType("mail.reply"), steps=[noop])
    minted = []
    known = {KNOWN: "5"}
    return (db, reg, clock, (lambda e: known.get(e.strip().lower())),
            (lambda e: minted.append(e) or "99"), minted)


def _turns(db):
    return db.execute("SELECT event_type FROM reaction WHERE event_type = 'mail.reply'")


@pytest.mark.parametrize("auth_results", [AR_FAIL, None], ids=["dmarc-fail", "no-auth-results"])
def test_inbound_mail_without_dmarc_pass_is_quarantined(auth_results):
    """A `From:` naming a known user, without a DMARC pass for that domain, is not that user: no
    `known_user_mail`, no agent turn in their session — it is held like a stranger's mail."""
    db, reg, clock, known, provision, minted = _mail_rig()
    headers = {"Authentication-Results": auth_results} if auth_results else {}
    out = handle(db, reg, clock, SELF, _mail(KNOWN, headers), known, lambda u: True, provision,
                 rules={})
    assert out is not None and out[0] != "known_user_mail", (
        f"unauthenticated mail claiming {KNOWN} was routed as that user: {out}")
    assert _turns(db) == [], "an agent turn was admitted for unauthenticated mail"
    assert minted == []


def test_inbound_mail_with_dmarc_pass_from_known_user_is_admitted():
    """Positive control: an authenticated mail from a known user still reaches their session."""
    db, reg, clock, known, provision, minted = _mail_rig()
    out = handle(db, reg, clock, SELF, _mail(KNOWN, {"Authentication-Results": AR_PASS}),
                 known, lambda u: True, provision, rules={})
    assert out == ("known_user_mail", 1)
    assert _turns(db) == [("mail.reply",)]


def test_known_user_mail_turn_runs_unattended_not_as_the_person(monkeypatch):
    """Question 4: the turn a mail causes is dispatched UNATTENDED (`X-Vexa-Flow-Regime:
    autonomous`), never as the person's own `human`-regime turn — so agent-api gives it the
    unattended (routine) delegation, not the person's interactive authority."""
    seen = []

    def fake_http(method, url, headers, body=None, timeout=20):
        seen.append((method, url, dict(headers), body))
        if url.endswith("/history"):
            return 200, {"turns": []}
        return 200, {}

    monkeypatch.setattr(ag, "agent_door", lambda: "http://agent-api.test")
    monkeypatch.setattr(ag, "require_internal_secret", lambda: "test-internal-secret")
    monkeypatch.setattr(ag, "http", fake_http)
    ag.dispatch_turn("5", "main", "[email-reply] ...", flow="email_chat", step="feedback_turn")
    (_, url, headers, _body), = [c for c in seen if c[0] == "POST"]
    assert url.endswith("/api/chat")
    assert headers.get("X-Vexa-Flow-Regime") == "autonomous"
    assert headers.get("X-Vexa-Flow-Regime") != "human"


# ── H8 · a routine is armed by a person, not by the agent that drafted it ───────────────────────

def _worker_answer(regime):
    return 200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["bot", "tx"],
                 "is_admin": False, "person_is_admin": False,
                 "delegation": {"regime": regime, "workspaces": "*"}}


@pytest.fixture
def api(monkeypatch):
    table = {"vxd_human": _worker_answer("human"), "vxd_auto": _worker_answer("autonomous")}
    monkeypatch.setattr(subject_auth, "_validate",
                        lambda base, token, secret: table.get(token, (401, {"detail": "no"})))
    from flows_steps import common, routine
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    monkeypatch.setattr(routine, "http", lambda m, u, h, b=None, timeout=20: (200, {"ok": True}))
    fa.db.execute("DELETE FROM flow_version WHERE scope = 'user:126'")
    yield TestClient(fa.app, raise_server_exceptions=False)
    fa.db.execute("DELETE FROM flow_version WHERE scope = 'user:126'")


ROUTINE = {"name": "annas-brief", "trigger": {"kind": "schedule", "cron": "0 7 * * *"},
           "steps": ["agent_run"], "params": {"instruction": "brief me"}}


def _status(name="annas-brief"):
    return [r[0] for r in fa.db.execute(
        "SELECT status FROM flow_version WHERE scope='user:126' AND name=:n", {"n": name})]


def test_unwatched_agent_cannot_create_a_flow(api):
    """Control (protection exists): a worker with nobody in the loop is refused `POST /flows`."""
    r = api.post("/flows", json=ROUTINE, headers={"Authorization": "Bearer vxd_auto"})
    assert r.status_code == 403
    assert r.json()["detail"] == subject_auth.REFUSAL
    assert _status() == []


@pytest.mark.parametrize("activate", [None, True], ids=["default", "explicit-activate"])
def test_agent_created_flow_is_inactive_until_person_approves(api, activate):
    """A flow an agent turn submits is a proposal: stored inactive until the person approves it,
    whatever `activate` the agent sent."""
    body = dict(ROUTINE, **({} if activate is None else {"activate": activate}))
    r = api.post("/flows", json=body, headers={"Authorization": "Bearer vxd_human"})
    assert r.status_code in (200, 201, 202, 403), r.text[:200]
    assert "active" not in _status(), (
        f"an agent-submitted flow is live with no person approval: {r.json()}")


def test_agent_cannot_activate_flow_without_session_approval(api):
    """Arming a drafted flow is the person's act: an agent turn calling `activate` on a draft is
    refused, and the draft stays a draft."""
    fa.db.execute("INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, "
                  "status, created_by, created_at) VALUES ('user:126','annas-brief',1,"
                  "'schedule.due','0 7 * * *','[\"agent_run\"]','{\"instruction\":\"x\"}',"
                  "'draft','t',1.0)")
    r = api.post("/flows/annas-brief/1/activate", headers={"Authorization": "Bearer vxd_human"})
    assert _status() == ["draft"], (
        f"an agent turn activated a draft with no person approval: {r.status_code} {r.text[:200]}")


# ── deny test 34 (PRD 0.13.4 decision 4): a DMARC failure is dropped with a quiet receipt ──────
def test_dmarc_failing_mail_gets_a_quiet_receipt_and_no_reply_notice_or_turn(monkeypatch):
    """Even with the quarantine reply switched ON, a mail its domain did not vouch for is answered by
    nothing: the reply would reach whoever was impersonated. The quarantine row is the receipt."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_QUARANTINE_REPLY", "true")
    db, reg, clock, known, provision, minted = _mail_rig()
    sent = []
    out = handle(db, reg, clock, SELF, _mail(KNOWN, {"Authentication-Results": AR_FAIL}), known,
                 lambda u: True, provision, rules={}, reply=lambda *a: sent.append(a))
    assert out == ("quarantine", 0), out
    assert sent == [], "a DMARC-failing mail was answered"
    assert _turns(db) == [] and minted == []
    rows = db.execute("SELECT kind FROM mail_quarantine")
    assert rows and rows[0][0] == "unauthenticated_mail", rows
