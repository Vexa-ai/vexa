"""VEXA_MAIL_RECIPIENT_ALLOW (PRD 0.13.4 S2; founder: nothing goes to anyone without his go).

Set, a message to a recipient not on the list is NOT sent: it goes to the capture double and is logged
`mail_diverted` with no content. Every kind (sign-in, invites, nudges, digests, recaps — the whole
`mailkinds` catalogue), the RSVP reply and the Graph channel pass the guard. Unset: no restriction.
Offline: SMTP is a recording double; nothing leaves the process.
"""
from __future__ import annotations

import logging

import pytest

from flows_steps import emailx, mailkinds, notify, outbound

RELAY = ("smtp.relay.test-provider.net", 587)
CAPTURE = ("mailpit", 1025)


class _SMTP:
    """smtplib.SMTP / SMTP_SSL double: records where it connected and what it sent."""
    made: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.sent = host, port, []
        _SMTP.made.append(self)

    def ehlo(self):
        pass

    def has_extn(self, name):
        return name.lower() == "starttls" and (self.host, self.port) == RELAY

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        self.sent.append(msg)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def smtp(monkeypatch):
    _SMTP.made = []
    monkeypatch.setattr(emailx.smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(emailx.smtplib, "SMTP_SSL", _SMTP)
    for k in ("SECURE", "TLS_INSECURE", "USER", "PASSWORD", "PASSWORD_REF", "TLS", "CA_FILE"):
        monkeypatch.delenv(f"VEXA_MAIL_SMTP_{k}", raising=False)
    monkeypatch.setenv("VEXA_MAIL_RELAY", "relay")
    monkeypatch.setenv("VEXA_MAIL_SMTP_HOST", RELAY[0])
    monkeypatch.setenv("VEXA_MAIL_SMTP_PORT", str(RELAY[1]))
    monkeypatch.setenv("VEXA_MAIL_CAPTURE_SMTP", f"{CAPTURE[0]}:{CAPTURE[1]}")
    monkeypatch.setenv("VEXA_MAIL_ADDR", "sender@vexa.ai")
    monkeypatch.setenv("VEXA_MAIL_RECIPIENT_ALLOW", "dmitry@vexa.ai")
    monkeypatch.setenv("VEXA_NOTIFY_CHANNEL", "smtp")
    notify.use(None)
    yield _SMTP
    notify.use(None)


def _where(s):
    return [((c.host, c.port), [m["To"] for m in c.sent]) for c in s.made if c.sent]


def test_an_allowed_address_is_sent_through_the_relay(smtp):
    emailx.send("dmitry@vexa.ai", "s", "b")
    assert _where(smtp) == [(RELAY, ["dmitry@vexa.ai"])]


def test_anybody_else_is_diverted_and_logged_without_content(smtp, caplog):
    caplog.set_level(logging.WARNING, logger="flows.mail")
    emailx.send("marvin@bank.example-customer.org", "Secret subject", "the body says 41 dollars")
    assert _where(smtp) == [(CAPTURE, ["marvin@bank.example-customer.org"])]
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "mail_diverted" in line and "to_domain=bank.example-customer.org" in line
    assert "Secret subject" not in line and "41 dollars" not in line and "marvin@" not in line


@pytest.mark.parametrize("allow,to,ok", [
    ("dmitry@vexa.ai", "Dmitry <DMITRY@vexa.ai>", True),
    ("@vexa.ai", "anna@vexa.ai", True),
    ("vexa.ai", "anna@vexa.ai", True),
    ("vexa.ai", "anna@sub.vexa.ai", False),
    ("dmitry@vexa.ai", "dmitry@vexa.ai.evil.org", False),
    ("", "anyone@anywhere.org", True),
])
def test_the_list_matching(monkeypatch, allow, to, ok):
    monkeypatch.setenv("VEXA_MAIL_RECIPIENT_ALLOW", allow)
    assert emailx.recipient_allowed(to) is ok


def test_unset_means_no_restriction(smtp, monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_RECIPIENT_ALLOW")
    emailx.send("someone@partner-org.net", "s", "b")
    assert _where(smtp) == [(RELAY, ["someone@partner-org.net"])]


@pytest.mark.parametrize("kind", sorted(mailkinds.KINDS))
def test_every_mail_kind_passes_the_guard(smtp, kind):
    """Sign-in, invites, nudges, digests, recaps — every kind in the catalogue, as a test-send (the
    switch and the cap do not apply), goes to the capture double for a recipient off the list."""
    out = outbound.send(kind, "valeriia@partner-org.net", subject="s", body="b",
                        link="https://app.example.test/x", test=True)
    assert out.result == outbound.SENT, out
    assert _where(smtp) == [(CAPTURE, ["valeriia@partner-org.net"])]


def test_the_rsvp_reply_passes_the_guard(smtp):
    emailx.send_rsvp_accept("organizer@partner-org.net", ics_uid="u1", start_epoch=1_800_000_000.0,
                            title="Sync")
    assert _where(smtp) == [(CAPTURE, ["organizer@partner-org.net"])]


def test_the_graph_channel_passes_the_guard(smtp, monkeypatch):
    called = []

    class _Graph:
        def send(self, *a, **k):
            called.append(a)
            return "graph-id"
    import flows_integrations.graph_client as gc
    monkeypatch.setattr(gc, "client", lambda: _Graph())
    notify.GraphNotifier().send("outsider@partner-org.net", "s", "b")
    assert called == [] and _where(smtp) == [(CAPTURE, ["outsider@partner-org.net"])]
    notify.GraphNotifier().send("dmitry@vexa.ai", "s", "b")
    assert len(called) == 1
