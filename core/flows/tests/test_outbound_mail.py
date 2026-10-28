"""THE ONE OUTBOUND MAIL PATH (`flows_steps/outbound.py`) — global mail, founder 2026-10-14.

  1. The shipped switches: sign-in, sharing and call invites ON; notices, minutes, reports OFF.
  2. A kind that is off sends nothing, raises nothing, and is audited `off`.
  3. The sign-in link cannot be switched off by the page.
  4. `admins` sends only to an admin; `mail_caps` caps a kind per day, under the operator's ceiling.
  5. Every attempt is one `mail_send` row: kind, template version, recipient DOMAIN, result —
     never the address.
  6. An edited template is a new version.
  7. Identity is operator config (From, Reply-To); the relay switch: `capture` reaches only the
     capture double, `relay` never relays a test domain.
  8. No step module sends past it: no bare `notify(` in `flows_defs`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows_steps.emailx as emailx  # noqa: E402
from flows_steps import mailkinds, mailtext, outbound, policies  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

pytestmark = pytest.mark.mail_defaults


class _Ch:
    def __init__(self):
        self.sent = []

    def __call__(self, to, subject, body, *, link=None, in_reply_to=None):
        self.sent.append({"to": to, "subject": subject, "body": body, "link": link})
        return f"<m{len(self.sent)}@vexa.test>"


@pytest.fixture
def page(monkeypatch):
    """The company's POLICIES front matter (as text) and its `_global/mail` overrides."""
    state = {"front": "", "files": {}}
    monkeypatch.setattr(policies, "read", lambda uid: policies.resolve(
        f"---\n{state['front']}\n---\n" if state["front"] else None))
    monkeypatch.setattr(mailtext, "_global_file", lambda uid, path: state["files"].get(path))
    monkeypatch.delenv("VEXA_MAIL_RELAY", raising=False)
    return state


def _rows(db):
    return db.execute("SELECT kind, template_version, recipient_domain, result, test FROM mail_send "
                      "ORDER BY at")


def test_the_shipped_switches_are_conservative():
    on = {k for k, v in mailkinds.KINDS.items() if v.default == "on"}
    assert {"sign-in", "workspace-invite", "meeting-share", "chat-share", "call-invite"} <= on
    for k in ("bot-scheduled", "bot-joined", "bot-failed", "minutes", "attendee-report",
              "prepare", "recording", "report-daily", "report-weekly", "digest"):
        assert mailkinds.KINDS[k].default == "off", k
    assert policies.resolve(None)["mail.minutes"] == "off"
    assert policies.resolve(None)["mail.workspace-invite"] == "on"


def test_every_templated_kind_has_a_starter_and_documents_its_variables():
    for k in mailkinds.KINDS.values():
        if k.template is None:
            continue
        raw = mailtext.DEFAULTS[k.template]
        assert raw.lower().startswith("subject:"), k.name
        used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", raw)) - set(mailkinds.COMMON_VARIABLES)
        assert used <= set(k.variables), f"{k.name}: undocumented {used - set(k.variables)}"
        assert set(k.variables) <= set(k.sample), f"{k.name}: a variable without a sample"


def test_a_kind_that_is_off_sends_nothing_and_is_audited(page):
    db, ch = SqliteDB(), _Ch()
    out = outbound.send("minutes", "anna@bank.test", subject="Minutes: X", body="b", db=db,
                        sender=ch)
    assert not out.sent and out.result == "off" and ch.sent == []
    assert _rows(db) == [("minutes", out.template_version, "bank.test", "off", 0)]


def test_switching_a_kind_on_sends_it(page):
    page["front"] = "mail.minutes: on"
    db, ch = SqliteDB(), _Ch()
    out = outbound.send("minutes", "anna@bank.test", subject="Minutes: X", body="b", db=db,
                        sender=ch)
    assert out.sent and len(ch.sent) == 1
    assert _rows(db)[0][3] == "sent"


def test_the_page_cannot_switch_the_sign_in_link_off(page):
    page["front"] = "mail.sign-in: off"
    db, ch = SqliteDB(), _Ch()
    out = outbound.send("sign-in", "anna@bank.test", values={"minutes": "15"},
                        link="https://x/redeem", db=db, sender=ch)
    assert out.sent and ch.sent[0]["subject"] == "Your Vexa sign-in link"
    assert "15 minutes" in ch.sent[0]["body"]


def test_admins_sends_only_to_an_admin(page):
    page["front"] = "mail.digest: admins"
    db, ch = SqliteDB(), _Ch()
    vals = {"count": "1", "items": "- x"}
    no = outbound.send("digest", "anna@bank.test", values=vals, db=db, sender=ch,
                       is_admin=lambda a: False)
    yes = outbound.send("digest", "admin@bank.test", values=vals, db=db, sender=ch,
                        is_admin=lambda a: a == "admin@bank.test")
    assert no.result == "admins-only" and yes.sent and [m["to"] for m in ch.sent] == ["admin@bank.test"]


def test_a_daily_cap_stops_the_kind_and_the_operator_ceiling_bounds_it(page, monkeypatch):
    page["front"] = "mail.minutes: on\nmail_caps: minutes=2, *=100"
    db, ch = SqliteDB(), _Ch()
    results = [outbound.send("minutes", f"p{i}@bank.test", subject="s", body="b", db=db,
                             sender=ch, now=1_000_000.0 + i).result for i in range(3)]
    assert results == ["sent", "sent", "capped"]
    monkeypatch.setenv("VEXA_MAIL_MAX_PER_DAY", "1")
    assert outbound.cap_for(policies.read("x"), "minutes") == 1


def test_the_audit_never_holds_the_address(page):
    page["front"] = "mail.minutes: on"
    db = SqliteDB()
    outbound.send("minutes", "anna.secret@bank.test", subject="s", body="b", db=db, sender=_Ch())
    assert "anna.secret" not in repr(db.execute("SELECT * FROM mail_send"))


def test_an_edited_template_is_a_new_version(page):
    before = outbound.template_version("workspace-invite")
    page["files"]["mail/workspace-invite.md"] = "subject: Hi {{inviter}}\n---\nJoin us."
    after = outbound.template_version("workspace-invite")
    assert before != after
    pv = outbound.preview("workspace-invite")
    assert pv["subject"] == "Hi Anna Example" and pv["source"] == "_global"
    assert pv["template_version"] == after


def test_a_transport_failure_is_audited_and_raised(page):
    db = SqliteDB()

    def broken(*a, **k):
        raise RuntimeError("relay down")
    with pytest.raises(RuntimeError):
        outbound.send("workspace-invite", "x@bank.test", subject="s", body="b", db=db,
                      sender=broken)
    assert _rows(db)[0][3] == "failed"


# ── the transport: identity and the relay switch ─────────────────────────────────────────────────

class _SMTP:
    made = []

    def __init__(self, host, port, timeout=None, **_):
        self.host, self.port, self.msgs = host, port, []
        _SMTP.made.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def has_extn(self, _):
        return False

    def send_message(self, m):
        self.msgs.append(m)

    def close(self):
        pass


def test_identity_and_capture_mode(monkeypatch):
    _SMTP.made = []
    monkeypatch.setattr(emailx.smtplib, "SMTP", _SMTP)
    monkeypatch.setenv("VEXA_MAIL_ADDR", "vexa@storm.test")
    monkeypatch.setenv("VEXA_MAIL_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("VEXA_MAIL_SMTP_FROM", "Vexa <info@vexa.ai>")
    monkeypatch.setenv("VEXA_MAIL_REPLY_TO", "info@vexa.ai")
    monkeypatch.setenv("VEXA_MAIL_RELAY", "capture")
    monkeypatch.setenv("VEXA_MAIL_CAPTURE_SMTP", "mailpit:1025")
    mid = emailx.send("someone@bank.test", "Subj", "Body")
    (conn,) = _SMTP.made
    assert (conn.host, conn.port) == ("mailpit", 1025), "capture never reaches the real relay"
    m = conn.msgs[0]
    assert m["From"] == "Vexa <info@vexa.ai>" and m["Reply-To"] == "info@vexa.ai"
    assert mid.endswith("@vexa.ai>")


def test_relay_mode_never_relays_a_test_domain(monkeypatch, page):
    _SMTP.made = []
    monkeypatch.setattr(emailx.smtplib, "SMTP", _SMTP)
    monkeypatch.setenv("VEXA_MAIL_RELAY", "relay")
    for to in ("a@bank.test", "b@example.com", "c@x.invalid", "d@host.localhost"):
        with pytest.raises(emailx.MailSuppressed):
            emailx.send(to, "s", "b")
    db = SqliteDB()
    out = outbound.send("workspace-invite", "a@bank.test", subject="s", body="b", db=db,
                        sender=_Ch())
    assert out.result == "suppressed" and _rows(db)[0][3] == "suppressed"
    assert _SMTP.made == []


def test_an_unknown_relay_mode_is_refused(monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_RELAY", "gmail")
    with pytest.raises(Exception, match="VEXA_MAIL_RELAY"):
        emailx.relay_mode()


# ── nothing sends past the one path ──────────────────────────────────────────────────────────────

def test_no_step_module_sends_past_the_outbound_path():
    defs = Path(__file__).resolve().parents[1] / "src" / "flows_defs"
    for f in defs.glob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"(?<![\w.])notify\(|p\.notify\(|send_rsvp_accept\(|emailx\.send\(|mx\.send\(",
                         code) and "lambda" not in code and "def " not in code:
                raise AssertionError(f"{f.name}:{n} sends past outbound: {line.strip()}")


# ── WHAT HAPPENS TO MAIL: delivered or captured (live defect, 2026-10-14) ─────────────────────────
# The agent said "the invite link was emailed to her" while the mail sat in the capture double.

def test_delivery_mode_names_a_capture_double_as_captured(monkeypatch):
    for k in ("VEXA_MAIL_RELAY", "VEXA_MAIL_SMTP_HOST", "VEXA_MAIL_CAPTURE_SMTP", "VEXA_NOTIFY_CHANNEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VEXA_MAIL_RELAY", "capture")
    assert emailx.delivery_mode() == "captured"
    monkeypatch.setenv("VEXA_MAIL_RELAY", "relay")
    monkeypatch.setenv("VEXA_MAIL_SMTP_HOST", "smtp.gmail.com")
    assert emailx.delivery_mode() == "delivered"
    # unset switch, relay pointed at the capture double (app.dev before the switch): captured
    monkeypatch.delenv("VEXA_MAIL_RELAY")
    monkeypatch.setenv("VEXA_MAIL_SMTP_HOST", "mailpit")
    assert emailx.delivery_mode() == "captured"
    monkeypatch.setenv("VEXA_MAIL_SMTP_HOST", "smtp.example.org")
    assert emailx.delivery_mode() == "delivered"


def test_a_sent_mail_says_whether_it_was_captured(page, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_RELAY", "capture")
    out = outbound.send("workspace-invite", "x@bank.com", subject="s", body="b", db=SqliteDB(),
                        sender=_Ch())
    assert out.sent and out.delivery == "captured"
    monkeypatch.setenv("VEXA_MAIL_RELAY", "relay")
    out = outbound.send("workspace-invite", "x@bank.com", subject="s", body="b", db=SqliteDB(),
                        sender=_Ch())
    assert out.delivery == "delivered"
