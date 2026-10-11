"""THE ADMIN ACCOUNT CONTROLS THE DEPLOYMENT'S MAIL — founder ruling 2026-10-11.

*"the global account functions: global flows setup via mcp (vexa@bank.com email invite and incoming
outgoing emails control and setup) … friction reading from sink"*.

The runtime half of mail lives in `_global/POLICIES.md`, which only the admin account writes
(agent-api's `_global` door; `test_admin_account.py` in core/agent proves the refusal). This file
proves the half flows owns — that what the admin account writes there is what the mailbox and the
senders actually do — plus the two operator-gated reads the admin account's MCP tools spend the
operator key on (`mail_settings`, `friction_list`):

  1. `mail_domains` on the page is the inbound allow-list, ahead of the operator's env value;
     a value that is not a domain is ignored, never read as "everyone";
  2. `bot_joins_on_invite: off` records an invitation and creates nothing;
  3. `minutes_mail: off` stops the minutes mail for everybody, whatever a person's own setting;
  4. `GET /mail/settings` reports all of it and no credential;
  5. `GET /friction/all` reads every person's reports, filtered and SCRUBBED, behind the operator
     key only.

Each test fails on the code this branch was cut from: `mail_domains`/`minutes_mail` did not exist,
`handle` took no rules, and neither route was served.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import Done, EventType, FakeClock, Reaction, Registry, StepCtx  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402
from flows_integrations import mail_policy  # noqa: E402
from flows_integrations.mailbox import handle  # noqa: E402
from flows_steps import policies  # noqa: E402

SELF = "vexa@bank.test"
ICS = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:u-7\nDTSTART:20300302T140000Z\n"
       "ORGANIZER:mailto:{org}\nATTENDEE:mailto:someone@bank.test\nSUMMARY:Pilot sync\n"
       "LOCATION:https://meet.google.com/abc-defg-hij\nEND:VEVENT\nEND:VCALENDAR\n")


def _page(**rows) -> str:
    body = "\n".join(f"{k}: {v}" for k, v in rows.items())
    return f"---\nkind: policies\n{body}\n---\n# Policies\n"


def _msg(frm, *, ics=None, mid=None):
    return SimpleNamespace(frm=frm, body="hello", subject="hi", headers={}, ics=ics,
                           message_id=mid or f"<{frm}-1@x.test>", ext_id="ext-1", cursor="c1")


def _rig():
    db, clock, reg = SqliteDB(), FakeClock(), Registry()

    @reg.step
    def noop(ctx):
        return Done()

    reg.flow(name="invite_intake", version=1, on=EventType("invite.received"), steps=[noop])
    reg.flow(name="email_chat", version=1, on=EventType("mail.reply"), steps=[noop])
    minted = []
    return db, reg, clock, (lambda e: None), (lambda e: minted.append(e) or "99"), minted


def _quarantined(db):
    return db.execute("SELECT from_addr, kind FROM mail_quarantine")


def _admitted(db):
    return db.execute("SELECT event_type FROM reaction")


# ── 1 · the inbound allow-list is the admin account's answer ─────────────────────────────────

def test_mail_domains_is_a_rule_with_an_empty_default():
    assert policies.DEFAULTS["mail_domains"] == ()
    assert policies.resolve(None)["mail_domains"] == ()


def test_a_value_that_is_not_a_domain_is_ignored_and_named():
    out = policies.resolve(_page(mail_domains="*, bank.test, com, @partner.test, x@y.test"))
    assert out["mail_domains"] == ("bank.test", "partner.test")
    assert any("'*'" in p for p in out["problems"])
    assert any("'com'" in p for p in out["problems"])


def test_the_page_wins_over_the_env(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_MAIL_DOMAINS", "other.test")
    rules = policies.resolve(_page(mail_domains="bank.test"))
    assert mail_policy.allow_domains(SELF, rules) == {"bank.test"}


def test_an_empty_page_falls_back_to_the_env_then_the_mailbox(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_MAIL_DOMAINS", "other.test")
    assert mail_policy.allow_domains(SELF, policies.resolve(None)) == {"other.test"}
    monkeypatch.delenv("VEXA_FLOWS_MAIL_DOMAINS")
    assert mail_policy.allow_domains(SELF, policies.resolve(None)) == {"bank.test"}


def test_only_accept_invites_from_bank_test(monkeypatch):
    """*"only accept invites from bank.com"* — written once on the page, read by the mailbox."""
    monkeypatch.setenv("VEXA_FLOWS_MAIL_DOMAINS", "bank.test,partner.test")
    rules = policies.resolve(_page(mail_domains="bank.test"))
    db, reg, clock, known, provision, minted = _rig()
    ok = handle(db, reg, clock, SELF, _msg("anna@bank.test", ics=ICS.format(org="anna@bank.test")),
                known, lambda u: False, provision, rules=rules)
    no = handle(db, reg, clock, SELF, _msg("ben@partner.test", ics=ICS.format(org="ben@partner.test"),
                                           mid="<b@x>"),
                known, lambda u: False, provision, rules=rules)
    assert ok[0] == "invite" and ok[1] == 1
    assert no == ("invite_quarantine", 0)
    assert [r[0] for r in _admitted(db)] == ["invite.received"]
    assert minted == []


def test_the_mailbox_reads_the_company_rules_when_none_are_handed_in(monkeypatch):
    seen = []

    def read(uid, path, slug=None):
        seen.append((uid, path, slug))
        return _page(mail_domains="bank.test") if path == "POLICIES.md" else None

    monkeypatch.setattr(policies, "ws_file", read)
    monkeypatch.delenv("VEXA_FLOWS_MAIL_DOMAINS", raising=False)
    db, reg, clock, known, provision, minted = _rig()
    out = handle(db, reg, clock, "vexa@elsewhere.test",
                 _msg("anna@bank.test", ics=ICS.format(org="anna@bank.test")),
                 known, lambda u: False, provision)
    assert out[0] == "invite"
    assert seen and seen[0] == (policies.COMPANY_READER, "POLICIES.md", "_global")


# ── 2 · invitations can be switched off ──────────────────────────────────────────────────────

def test_bot_joins_on_invite_off_records_the_invitation_and_creates_nothing():
    rules = policies.resolve(_page(bot_joins_on_invite="off"))
    db, reg, clock, known, provision, minted = _rig()
    out = handle(db, reg, clock, SELF, _msg("anna@bank.test", ics=ICS.format(org="anna@bank.test")),
                 known, lambda u: False, provision, rules=rules)
    assert out == ("invite_policy_off", 0)
    assert _admitted(db) == []
    assert [tuple(r) for r in _quarantined(db)] == [("anna@bank.test", mail_policy.INVITE_POLICY_OFF)]
    assert minted == []


def test_bot_joins_on_invite_off_leaves_ordinary_mail_alone():
    rules = policies.resolve(_page(bot_joins_on_invite="off"))
    db, reg, clock, known, provision, minted = _rig()
    out = handle(db, reg, clock, SELF, _msg("anna@bank.test"), known, lambda u: False, provision,
                 rules=rules)
    assert out[0] == "new_sender_mail"


# ── 3 · the minutes mail can be switched off for everybody ───────────────────────────────────

def _ctx(refs):
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior={"process_meeting": {"report": "x"}},
                   clock_now=1_700_000_000.0, scratch={}, flow=None)


def test_minutes_mail_off_stops_the_minutes_mail_whatever_the_person_says(monkeypatch):
    import flows_defs.production as production
    from test_link_loop import _StubDB
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(policies, "ws_file",
                        lambda uid, path, slug=None: _page(minutes_mail="off")
                        if path == "POLICIES.md" else None)
    asked = []
    monkeypatch.setattr(production, "setting", lambda uid, key: asked.append(key) or True)
    out = reg.steps["email_minutes"](_ctx({"uid": "7", "organizer": "anna@bank.test",
                                           "title": "Pilot", "meeting_id": 97}))
    assert isinstance(out, Done)
    assert out.result == {"skipped": "minutes_mail is off for this deployment"}
    assert "mail_minutes" not in asked


def test_minutes_mail_is_on_by_default():
    assert policies.DEFAULTS["minutes_mail"] is True


# ── 4 · the mail settings, read whole, with no credential in them ───────────────────────────

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-admin-account",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://admin-account:unreachable@127.0.0.1:1/flows"}
_OP_KEY: list = []


@pytest.fixture(scope="module")
def _app():
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
    _OP_KEY[:] = [flows_api.API_KEY]
    return TestClient(flows_api.app, raise_server_exceptions=False), flows_api


@pytest.fixture()
def api(_app):
    _client, flows_api = _app
    flows_api.db.execute("DELETE FROM reaction")
    yield _app
    flows_api.db.execute("DELETE FROM reaction")


def _op():
    return {"X-Flows-Operator-Key": _OP_KEY[0]}


def test_mail_settings_needs_the_operator_key(api):
    client, _ = api
    assert client.get("/mail/settings").status_code == 401
    assert client.get("/mail/settings", headers={"Authorization": "Bearer vx_person"}).status_code == 401


def test_mail_settings_reports_the_page_and_never_a_credential(api, monkeypatch):
    client, _ = api
    monkeypatch.setenv("VEXA_MAIL_ADDR", "vexa@bank.test")
    monkeypatch.setenv("VEXA_MAIL_APP_PASSWORD", "pw-THIS-MUST-NOT-APPEAR-123")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "smtp-THIS-MUST-NOT-APPEAR-456")
    monkeypatch.setattr(policies, "ws_file",
                        lambda uid, path, slug=None: _page(mail_domains="bank.test",
                                                           minutes_mail="off")
                        if path == "POLICIES.md" else None)
    import flows_steps.common as common
    monkeypatch.setattr(common, "ws_file", lambda uid, path, slug=None:
                        "subject: x\n---\nours" if path == "mail/prepare.md" else None)
    r = client.get("/mail/settings", headers=_op())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mailbox"]["address"] == "vexa@bank.test"
    assert body["mailbox"]["credentials_configured"] is True
    assert body["inbound"]["allow_list"] == ["bank.test"]
    assert body["inbound"]["allow_list_source"] == "POLICIES.md mail_domains"
    assert body["outbound"]["minutes"] is False and body["outbound"]["prep"] is True
    assert {t["name"]: t["source"] for t in body["templates"]}["prepare"] == "_global"
    assert "invite_intake" in {f["name"] for f in body["inbound"]["flows"]}
    assert "THIS-MUST-NOT-APPEAR" not in r.text


# ── 5 · the friction sink, read whole, scrubbed ──────────────────────────────────────────────

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def _file(client, uid, **body):
    r = client.post("/friction", json=body, headers={**_op(), "X-User-Id": uid})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_the_sink_is_operator_only(api):
    client, _ = api
    assert client.get("/friction/all").status_code == 401
    assert client.get("/friction/all", headers={"Authorization": "Bearer vx_person"}).status_code == 401


def test_the_sink_reads_everybody_filtered_and_scrubbed(api, monkeypatch):
    client, flows_api = api
    import flows_timeline.service as service
    monkeypatch.setattr(service, "resolve_identity",
                        lambda s: {"501": ("501", "anna@bank.test"),
                                   "anna@bank.test": ("501", "anna@bank.test"),
                                   "502": ("502", "ben@bank.test")}.get(str(s), ("", "")))
    a = _file(client, "501", what_i_tried=f"attach with {TOKEN}", what_happened="refused",
              kind="Refusal", note=f"token {TOKEN}")
    b = _file(client, "502", what_i_tried="send mail", what_happened="nothing", kind="error")
    r = client.get("/friction/all", headers=_op())
    assert r.status_code == 200, r.text
    ids = {x["id"] for x in r.json()["reports"]}
    assert {a, b} <= ids
    assert TOKEN not in r.text, "a token somebody pasted reached the admin account's read"
    only = client.get("/friction/all", params={"kind": "refusal"}, headers=_op()).json()
    assert {x["id"] for x in only["reports"]} == {a}
    mine = client.get("/friction/all", params={"person": "anna@bank.test"}, headers=_op()).json()
    assert {x["id"] for x in mine["reports"]} == {a}
    assert client.get("/friction/all", params={"status": "maybe"}, headers=_op()).status_code == 400
    fixed = client.post(f"/friction/{b}/fix", params={"fix_ref": "abc123"}, headers=_op())
    assert fixed.status_code == 201
    still_open = client.get("/friction/all", params={"status": "open"}, headers=_op()).json()
    assert b not in {x["id"] for x in still_open["reports"]}


def test_a_person_still_reads_only_their_own(api):
    client, _ = api
    r = client.get("/friction", headers=_op())
    assert r.status_code == 400   # an operator key that names nobody gets no instance-wide read here
