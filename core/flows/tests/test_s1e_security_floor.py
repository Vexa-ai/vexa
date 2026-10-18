"""0.13.4 slice S1 part E — the security floor's flows half (D24, D25).

* D24 — an agent turn proposes a flow; it is filed as a draft and only the person's own credential
  arms it. Narrowing (pause, retire) stays open to the agent.
* D13 — and that credential is a SESSION (identity's `credential_class`): a person's API key, whatever
  its scopes, is refused `session_required` when it arms (create-active, activate, resume).
* D25 — a `From:` is not a person until its domain vouches for it: the receiving MTA's DMARC verdict
  (or an aligned DKIM/SPF pass), read from the TOPMOST `Authentication-Results`, gates every branch
  that acts for the address; the turn it causes is a `mail` turn, which agent-api runs read-only.

In-process: the sqlite double, FakeClock, the identity hop stubbed. No containers, no network.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from test_sec_deny_2026_10_17 import (  # noqa: E402  (shares its env-pinned flows_api import)
    AR_PASS, KNOWN, ROUTINE, SELF, _mail, _mail_rig, _status, _turns, _worker_answer, fa,
    subject_auth)
from flows_integrations import inbox, mail_policy  # noqa: E402
from flows_integrations.mailbox import handle, route  # noqa: E402
from flows_steps import agent as ag  # noqa: E402


# ── D24 ──────────────────────────────────────────────────────────────────────────────────────────

def _person_answer():
    return 200, {"user_id": 126, "email": "anna@vexa.test", "scopes": ["bot", "tx"],
                 "is_admin": False, "credential_class": "session"}


@pytest.fixture
def api(monkeypatch):
    key = dict(_person_answer()[1], scopes=["bot", "tx", "browser"], credential_class="key")
    legacy = {k: v for k, v in _person_answer()[1].items() if k != "credential_class"}
    table = {"vxd_human": _worker_answer("human"), "vx_person": _person_answer(),
             "vx_key": (200, key), "vx_legacy": (200, legacy)}
    monkeypatch.setattr(subject_auth, "_validate",
                        lambda base, token, secret: table.get(token, (401, {"detail": "no"})))
    from flows_steps import common, routine
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    monkeypatch.setattr(routine, "http", lambda m, u, h, b=None, timeout=20: (200, {"ok": True}))
    fa.db.execute("DELETE FROM flow_version WHERE scope = 'user:126'")
    yield TestClient(fa.app, raise_server_exceptions=False)
    fa.db.execute("DELETE FROM flow_version WHERE scope = 'user:126'")


AGENT = {"Authorization": "Bearer vxd_human"}
PERSON = {"Authorization": "Bearer vx_person"}


def test_an_agents_submission_says_it_waits_for_the_person(api):
    r = api.post("/flows", json=ROUTINE, headers=AGENT)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft" and body["awaiting_approval"] is True
    assert body["live_within_s"] is None and "Routines board" in body["note"]


def test_the_persons_own_credential_still_arms_at_once(api):
    r = api.post("/flows", json=ROUTINE, headers=PERSON)
    assert r.status_code == 201 and r.json()["status"] == "active"
    assert "awaiting_approval" not in r.json()
    assert _status() == ["active"]


def test_the_person_arms_the_agents_draft(api):
    api.post("/flows", json=ROUTINE, headers=AGENT)
    r = api.post("/flows/annas-brief/1/activate", headers=PERSON)
    assert r.status_code == 200 and _status() == ["active"]


@pytest.mark.parametrize("action", ["activate", "resume"])
def test_an_agent_turn_never_arms_a_flow(api, action):
    api.post("/flows", json=ROUTINE, headers=PERSON)
    api.post("/flows/annas-brief/1/pause", headers=PERSON)
    r = api.post(f"/flows/annas-brief/1/{action}", headers=AGENT)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "person_approval_required"
    assert _status() == ["retired"]


def test_an_agent_turn_may_still_pause_a_flow(api):
    api.post("/flows", json=ROUTINE, headers=PERSON)
    r = api.post("/flows/annas-brief/1/pause", headers=AGENT)
    assert r.status_code == 200 and _status() == ["retired"]


def test_an_agents_extra_timer_is_a_proposal_too(api):
    r = api.post("/routines/triggers", headers=AGENT,
                 json={"name": "annas-noon", "thread": "main", "instruction": "check in",
                       "trigger": {"kind": "schedule", "cron": "0 12 * * *"}})
    assert r.status_code == 201, r.text
    assert r.json()["awaiting_approval"] is True and r.json()["active"] is None
    assert _status("annas-noon") == ["draft"]


# ── D13: a person's routine is armed from a SESSION, never from an API key ──────────────────────
KEY = {"Authorization": "Bearer vx_key"}          # the person's own API key, browser scope and all
LEGACY = {"Authorization": "Bearer vx_legacy"}    # an identity that states no credential class


@pytest.mark.parametrize("who", [KEY, LEGACY], ids=["key", "no-class"])
def test_a_key_cannot_create_an_active_routine(api, who):
    r = api.post("/flows", json=ROUTINE, headers=who)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "session_required"
    assert _status() == []


def test_a_key_may_still_file_a_draft(api):
    r = api.post("/flows", json={**ROUTINE, "activate": False}, headers=KEY)
    assert r.status_code == 201 and r.json()["status"] == "draft"


@pytest.mark.parametrize("who", [KEY, LEGACY], ids=["key", "no-class"])
def test_a_key_cannot_activate_a_routine(api, who):
    api.post("/flows", json={**ROUTINE, "activate": False}, headers=PERSON)
    r = api.post("/flows/annas-brief/1/activate", headers=who)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "session_required"
    assert _status() == ["draft"]


@pytest.mark.parametrize("who", [KEY, LEGACY], ids=["key", "no-class"])
def test_a_key_cannot_resume_a_routine_but_may_pause_it(api, who):
    api.post("/flows", json=ROUTINE, headers=PERSON)
    assert api.post("/flows/annas-brief/1/pause", headers=who).status_code == 200
    r = api.post("/flows/annas-brief/1/resume", headers=who)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "session_required"
    assert _status() == ["retired"]
    assert api.post("/flows/annas-brief/1/resume", headers=PERSON).status_code == 200
    assert _status() == ["active"]


def test_the_operator_still_arms_a_named_persons_routine(api):
    """The operator key (agent-api's routine sync) keeps its path: the session rule is the person's."""
    op = {"X-Flows-Operator-Key": fa.API_KEY}
    api.post("/flows", json={**ROUTINE, "activate": False}, headers=PERSON)
    r = api.post("/flows/annas-brief/1/activate?scope=user:126", headers=op)
    assert r.status_code == 200, r.text
    assert _status() == ["active"]


def test_the_caller_carries_identitys_class():
    from flows_integrations.subject_auth import Caller
    assert Caller(kind="subject", uid="1", credential_class="session").is_session
    assert not Caller(kind="subject", uid="1", credential_class="key").is_session
    assert not Caller(kind="subject", uid="1").is_session
    assert not Caller(kind="subject", uid="1", credential_class="session", delegation={}).is_session
    assert not Caller(kind="admin", credential_class="session").is_session


# ── D25: the verdict ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ar, ok", [
    (AR_PASS, True),
    ("mx.acme.test; dmarc=pass header.from=evil.test", False),            # a pass for ANOTHER domain
    ("mx.acme.test; dkim=pass header.d=acme.test", True),                 # aligned DKIM, no DMARC
    ("mx.acme.test; dkim=pass header.d=sub.acme.test", True),             # relaxed alignment
    ("mx.acme.test; dkim=pass header.d=evil.test", False),                # unaligned DKIM
    ("mx.acme.test; spf=pass smtp.mailfrom=bounce@acme.test", True),      # aligned SPF
    ("mx.acme.test; dkim=pass header.d=acme.test; dmarc=fail header.from=acme.test", False),
    ("mx.acme.test; dkim=none; spf=none", False),
    (None, False),
])
def test_the_domain_must_vouch_for_the_from(ar, ok):
    headers = {"Authentication-Results": ar} if ar else {}
    assert mail_policy.sender_authenticated(KNOWN, headers)[0] is ok


def test_a_named_authserv_id_ignores_a_verdict_from_anyone_else(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_MAIL_AUTHSERV_ID", "mx.google.com")
    assert mail_policy.sender_authenticated(KNOWN, {"Authentication-Results": AR_PASS})[0] is False
    good = "mx.google.com; dmarc=pass (p=NONE) header.from=acme.test"
    assert mail_policy.sender_authenticated(KNOWN, {"Authentication-Results": good})[0] is True


def test_the_topmost_verdict_is_ours_and_a_forged_one_below_is_ignored():
    """The receiving MTA prepends its verdict; a sender can write any header into the message, and
    `dict(msg.items())` used to keep the LAST — the sender's."""
    raw = ("Authentication-Results: mx.acme.test; dmarc=fail header.from=acme.test\r\n"
           "From: Anna <anna@acme.test>\r\n"
           "Authentication-Results: mx.acme.test; dmarc=pass header.from=acme.test\r\n"
           "Subject: hi\r\nMessage-ID: <m1@x>\r\n\r\nbody\r\n").encode()
    msg = inbox.from_rfc822(raw, cursor="1", ext_id="1")
    assert "dmarc=fail" in msg.headers["Authentication-Results"]
    assert mail_policy.sender_authenticated(msg.frm, msg.headers)[0] is False


# ── D25: every branch that acts for the address ──────────────────────────────────────────────────

def test_a_thread_reply_needs_the_verdict_too():
    db, *_ = _mail_rig()
    db.execute("INSERT INTO mail_thread (message_id, subject_uid, session, created_at) "
               "VALUES ('<t1@x>', '5', 'm1', 1.0)")
    known = {KNOWN: "5"}.get
    kind, p = route(db, SELF, KNOWN, {"In-Reply-To": "<t1@x>"}, None, known, lambda u: True,
                    allowed={"acme.test"})
    assert kind == "quarantine" and p["kind"] == mail_policy.UNAUTHENTICATED_MAIL
    kind, p = route(db, SELF, KNOWN, {"In-Reply-To": "<t1@x>", "Authentication-Results": AR_PASS},
                    None, known, lambda u: True, allowed={"acme.test"})
    assert kind == "thread_reply" and p["uid"] == "5"


def test_a_colleague_inside_our_own_domain_needs_the_verdict_too():
    """SF-3: the mailbox's own domain is no exception — a spoofed colleague would get an account."""
    db, reg, clock, _known, provision, minted = _mail_rig()
    out = handle(db, reg, clock, SELF, _mail("new@acme.test", {}), lambda e: None, lambda u: True,
                 provision, rules={})
    assert out == ("quarantine", 0) and minted == [] and _turns(db) == []


def test_a_mail_turn_is_dispatched_as_a_mail_turn(monkeypatch):
    seen = []
    monkeypatch.setattr(ag, "agent_door", lambda: "http://agent-api.test")
    monkeypatch.setattr(ag, "require_internal_secret", lambda: "s")
    monkeypatch.setattr(ag, "http", lambda m, u, h, b=None, timeout=20:
                        (seen.append((m, dict(h))) or (200, {"turns": []} if u.endswith("/history")
                                                       else {})))
    ag.dispatch_turn("5", "main", "x", flow="email_chat", step="feedback_turn", kind="mail")
    (_m, headers), = [c for c in seen if c[0] == "POST"]
    assert headers["X-Vexa-Flow-Kind"] == "mail"
    assert headers["X-Vexa-Flow-Regime"] == "autonomous"
