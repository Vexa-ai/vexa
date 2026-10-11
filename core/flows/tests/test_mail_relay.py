"""The deployment's mail relay is one family of keys for every sender (S2).

The terminal mails its sign-in links through VEXA_MAIL_SMTP_*, and so does flows. A relay an
operator configures once — a provider's implicit-TLS :465 with a login — therefore has to work for
flows as well, not only the plain, login-free mail double flows used to assume.
"""
from __future__ import annotations

import smtplib
import ssl

import pytest

from flows_steps import emailx
from flows_steps.mail_transport import MailTransportError


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
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 authentication failed")
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
    for k in ("SECURE", "TLS_INSECURE", "USER", "PASSWORD", "TLS", "CA_FILE"):
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
    with pytest.raises(MailTransportError, match=r"mail:auth smtp relay.example:2525 .*535") as ei:
        emailx._smtp()
    assert ei.value.kind == "auth" and not ei.value.retryable and "wrong" not in str(ei.value)
    assert relay["plain"].closed


def test_unset_host_is_still_gmail(relay, monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_SMTP_HOST")
    conn, gmail_login = emailx._smtp()
    assert conn is relay["tls"] and conn.args == ("smtp.gmail.com", 465) and gmail_login


# ── credentials only over an encrypted connection (A-12 / F-1) ─────────────────────────────────

def test_credentials_are_refused_on_a_relay_that_offers_no_encryption(relay, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "relay-user")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "relay-pass")
    with pytest.raises(MailTransportError, match="unencrypted") as ei:
        emailx._smtp()
    assert ei.value.kind == "config" and "relay-pass" not in str(ei.value)
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


# ── VEXA_MAIL_SMTP_TLS: the relay's TLS policy, stated rather than inferred ─────────────────────

def test_a_server_that_quotes_the_refused_auth_line_never_leaks_the_credential(relay, monkeypatch):
    """Some relays quote the AUTH line they refused, and for AUTH LOGIN that line is base64 of the
    user or the password. The fault masks both, raw and encoded (P14)."""
    import base64
    leaked = base64.b64encode(b"relay-pass").decode()

    def refuse(self, user, password):
        raise smtplib.SMTPAuthenticationError(500, f"bad line <AUTH LOGIN {leaked}> for relay-pass")
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setattr(_Conn, "login", refuse)
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "relay-user")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "relay-pass")
    with pytest.raises(MailTransportError) as ei:
        emailx._smtp()
    assert ei.value.kind == "auth"
    assert "relay-pass" not in str(ei.value) and leaked not in str(ei.value)
    assert "***" in str(ei.value)


def test_starttls_required_refuses_a_relay_that_does_not_offer_it(relay, monkeypatch):
    """`auto` would send in the clear to such a relay; `starttls` is the operator saying it must
    not. Fails on the previous code, which had no way to say so."""
    monkeypatch.setenv("VEXA_MAIL_SMTP_TLS", "starttls")
    with pytest.raises(MailTransportError, match="does not offer STARTTLS") as ei:
        emailx._smtp()
    assert ei.value.kind == "tls" and relay["plain"].closed


def test_starttls_required_upgrades_when_offered(relay, monkeypatch):
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setenv("VEXA_MAIL_SMTP_TLS", "starttls")
    conn, _ = emailx._smtp()
    assert conn.events == ["ehlo", "starttls", "ehlo"] and conn.encrypted


def test_tls_mode_is_implicit_tls_without_the_secure_flag(relay, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_TLS", "tls")
    conn, _ = emailx._smtp()
    assert conn is relay["tls"] and conn.kw["context"].verify_mode == ssl.CERT_REQUIRED


def test_none_never_upgrades_and_still_never_sends_credentials(relay, monkeypatch):
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setenv("VEXA_MAIL_SMTP_TLS", "none")
    conn, _ = emailx._smtp()
    assert "starttls" not in conn.events and not conn.encrypted
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "relay-user")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "relay-pass")
    with pytest.raises(MailTransportError, match="unencrypted"):
        emailx._smtp()


def test_an_unknown_tls_mode_is_refused_by_name(relay, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_TLS", "maybe")
    with pytest.raises(MailTransportError, match="VEXA_MAIL_SMTP_TLS='maybe'") as ei:
        emailx._smtp()
    assert ei.value.kind == "config"


def test_the_ca_file_is_what_starttls_trusts(relay, monkeypatch, tmp_path):
    """An internal CA: the bundle is loaded into the context the upgrade verifies against. A
    named bundle that does not exist is a config fault before any socket opens."""
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setenv("VEXA_MAIL_SMTP_CA_FILE", str(tmp_path / "missing.pem"))
    with pytest.raises(MailTransportError, match="does not exist") as ei:
        emailx._smtp()
    assert ei.value.kind == "config"
    ca = ssl.get_default_verify_paths().cafile
    if not ca:
        pytest.skip("no system CA bundle file to stand in for an internal one")
    monkeypatch.setenv("VEXA_MAIL_SMTP_CA_FILE", ca)
    conn, _ = emailx._smtp()
    assert conn.starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert conn.starttls_context.cert_store_stats()["x509_ca"] > 0
