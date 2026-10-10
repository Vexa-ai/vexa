"""The deployment's mail relay is one family of keys for every sender (S2).

The terminal mails its sign-in links through VEXA_MAIL_SMTP_*, and so does flows. A relay an
operator configures once — a provider's implicit-TLS :465 with a login — therefore has to work for
flows as well, not only the plain, login-free mail double flows used to assume.
"""
from __future__ import annotations

import ssl

import pytest

from flows_steps import emailx


class _Conn:
    #: whether a plain relay advertises STARTTLS after EHLO; the fixture sets it per case
    offers_starttls = False

    def __init__(self, *a, **kw):
        self.args, self.kw, self.logins, self.closed = a, kw, [], False
        self.encrypted = "context" in kw          # SMTP_SSL: encrypted from the first byte
        self.starttls_context = None
        self.events: list[str] = []

    def ehlo(self):
        self.events.append("ehlo")

    def has_extn(self, name):
        return name.lower() == "starttls" and self.offers_starttls and not self.encrypted

    def starttls(self, context=None):
        self.events.append("starttls")
        self.starttls_context = context
        self.encrypted = True

    def login(self, user, password):
        self.events.append("login-encrypted" if self.encrypted else "login-PLAINTEXT")
        if password == "wrong":
            raise RuntimeError("535 auth failed")
        self.logins.append((user, password))

    def close(self):
        self.closed = True


@pytest.fixture
def relay(monkeypatch):
    made = {}

    def plain(*a, **kw):
        made["plain"] = _Conn(*a, **kw)
        return made["plain"]

    def tls(*a, **kw):
        made["tls"] = _Conn(*a, **kw)
        return made["tls"]

    monkeypatch.setattr(_Conn, "offers_starttls", False)
    monkeypatch.setattr(emailx.smtplib, "SMTP", plain)
    monkeypatch.setattr(emailx.smtplib, "SMTP_SSL", tls)
    for k in ("SECURE", "TLS_INSECURE", "USER", "PASSWORD"):
        monkeypatch.delenv(f"VEXA_MAIL_SMTP_{k}", raising=False)
    monkeypatch.setenv("VEXA_MAIL_SMTP_HOST", "relay.example")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PORT", "2525")
    return made


def test_a_plain_relay_takes_no_login(relay):
    conn, gmail_login = emailx._smtp()
    assert conn is relay["plain"] and conn.args == ("relay.example", 2525) and not gmail_login
    assert conn.logins == []


def test_a_secure_relay_speaks_tls_and_logs_in_with_the_family_credentials(relay, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_SECURE", "1")
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "relay-user")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "relay-pass")
    conn, gmail_login = emailx._smtp()
    assert conn is relay["tls"] and conn.args == ("relay.example", 2525) and not gmail_login
    assert conn.kw["context"].verify_mode == ssl.CERT_REQUIRED
    assert conn.logins == [("relay-user", "relay-pass")]


def test_tls_insecure_is_only_the_certificate_check(relay, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_SECURE", "1")
    monkeypatch.setenv("VEXA_MAIL_SMTP_TLS_INSECURE", "1")
    conn, _ = emailx._smtp()
    assert conn.kw["context"].verify_mode == ssl.CERT_NONE and conn.logins == []


def test_a_refused_login_closes_the_connection_and_says_so(relay, monkeypatch):
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "relay-user")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "wrong")
    with pytest.raises(RuntimeError, match="535"):
        emailx._smtp()
    assert relay["plain"].closed


def test_unset_host_is_still_gmail(relay, monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_SMTP_HOST")
    conn, gmail_login = emailx._smtp()
    assert conn is relay["tls"] and conn.args == ("smtp.gmail.com", 465) and gmail_login


# ── credentials only over an encrypted connection (A-12 / F-1) ─────────────────────────────────

def test_credentials_are_refused_on_a_relay_that_offers_no_encryption(relay, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "relay-user")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "relay-pass")
    with pytest.raises(RuntimeError, match="unencrypted"):
        emailx._smtp()
    conn = relay["plain"]
    assert conn.logins == [] and "login-PLAINTEXT" not in conn.events and conn.closed


def test_a_relay_that_offers_starttls_is_upgraded_with_a_verified_certificate_before_login(relay, monkeypatch):
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "relay-user")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "relay-pass")
    conn, _ = emailx._smtp()
    assert conn.events == ["ehlo", "starttls", "ehlo", "login-encrypted"]
    assert conn.starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert conn.logins == [("relay-user", "relay-pass")]


def test_without_credentials_a_relay_that_offers_starttls_is_still_upgraded(relay, monkeypatch):
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    conn, _ = emailx._smtp()
    assert conn.events == ["ehlo", "starttls", "ehlo"] and conn.encrypted


def test_tls_insecure_reaches_starttls_too_and_only_the_check(relay, monkeypatch):
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setenv("VEXA_MAIL_SMTP_TLS_INSECURE", "1")
    conn, _ = emailx._smtp()
    assert conn.encrypted and conn.starttls_context.verify_mode == ssl.CERT_NONE
