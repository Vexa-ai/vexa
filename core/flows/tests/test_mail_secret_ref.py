"""The relay password by REFERENCE (PRD 0.13.4 S2): `VEXA_MAIL_SMTP_PASSWORD_REF=broker:mail/<name>`.

Outbound mail is configured per install (host, port, user, From in the environment); the password is
read from the credential broker with flows' broker key and is never a raw value in this process's
environment. Offline: the broker is a stub transport that checks the signed assertion; the relay is
the `_Conn` double from test_mail_relay. No mail is sent anywhere.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from flows_sensors import broker_assertion
from flows_steps import emailx, mail_secret
from flows_steps.mail_transport import MailTransportError

from test_mail_relay import _Conn, relay  # noqa: F401 — the relay fixture

KEY = "k" * 64


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Broker:
    """Answers `POST /api/internal/mail/smtp-credential` after verifying the sensors assertion."""

    def __init__(self, store=None, status=200):
        self.store, self.status, self.calls = store or {"smtp": "app-pass"}, status, []

    def __call__(self, req, timeout=None):
        body = req.data
        header = req.get_header(broker_assertion.HEADER.capitalize())
        claims = broker_assertion.verify(header, key_for=lambda role: KEY.encode(), method="POST",
                                         path=mail_secret.PATH, body=body, remember=lambda n, t: True)
        self.calls.append((req.full_url, json.loads(body), claims))
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(b"{}"))
        name = json.loads(body)["name"]
        if name not in self.store:
            raise urllib.error.HTTPError(req.full_url, 404, "x", {}, io.BytesIO(b"{}"))
        return _Resp(json.dumps({"name": name, "value": self.store[name]}).encode())


def _forget_everywhere():
    """Another test file re-imports `flows_steps` (fresh sys.modules), so the module `emailx`
    reaches may not be the one imported above: clear both caches."""
    import importlib
    import sys
    mail_secret.forget()
    live = sys.modules.get("flows_steps.mail_secret") or importlib.import_module("flows_steps.mail_secret")
    live.forget()


@pytest.fixture
def broker_env(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text(KEY + "\n")
    monkeypatch.setenv("VEXA_FLOWS_SENSORS_BROKER_URL", "http://broker:8100")
    monkeypatch.setenv("VEXA_FLOWS_SENSORS_KEY_FILE", str(key))
    monkeypatch.delenv("VEXA_MAIL_SMTP_PASSWORD", raising=False)
    _forget_everywhere()
    yield
    _forget_everywhere()


def test_no_ref_reads_the_raw_value_as_before(monkeypatch):
    monkeypatch.delenv("VEXA_MAIL_SMTP_PASSWORD_REF", raising=False)
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "raw")
    assert mail_secret.smtp_password() == "raw"


def test_a_ref_is_read_from_the_broker_with_the_flows_key(broker_env, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/smtp")
    b = _Broker()
    assert mail_secret.smtp_password(transport=b) == "app-pass"
    url, body, claims = b.calls[0]
    assert url == "http://broker:8100/api/internal/mail/smtp-credential" and body == {"name": "smtp"}
    assert claims["role"] == "sensors" and claims["actor"] == "flows-mail"
    # cached for five minutes: a second send does not ask again
    assert mail_secret.smtp_password(transport=b) == "app-pass" and len(b.calls) == 1


def test_the_ref_wins_over_a_stale_raw_value(broker_env, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/smtp")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD", "stale")
    assert mail_secret.smtp_password(transport=_Broker()) == "app-pass"


@pytest.mark.parametrize("bad", ["smtp", "broker:signals/x", "broker:mail/UPPER", "broker:mail/a/b",
                                 "env:VEXA_MAIL_SMTP_PASSWORD"])
def test_a_malformed_ref_is_a_config_fault_naming_the_key(broker_env, monkeypatch, bad):
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD_REF", bad)
    with pytest.raises(MailTransportError) as ei:
        mail_secret.smtp_password(transport=_Broker())
    assert ei.value.kind == "config" and not ei.value.retryable
    assert "VEXA_MAIL_SMTP_PASSWORD_REF" in str(ei.value)


def test_a_ref_without_a_broker_is_refused(monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/smtp")
    monkeypatch.delenv("VEXA_FLOWS_SENSORS_BROKER_URL", raising=False)
    mail_secret.forget()
    with pytest.raises(MailTransportError, match="no broker") as ei:
        mail_secret.smtp_password()
    assert ei.value.kind == "config"


def test_a_missing_name_says_how_to_store_it(broker_env, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/other")
    with pytest.raises(MailTransportError, match="mail_secret put other") as ei:
        mail_secret.smtp_password(transport=_Broker())
    assert ei.value.kind == "config"


def test_an_unreachable_broker_is_retried(broker_env, monkeypatch):
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/smtp")

    def down(req, timeout=None):
        raise urllib.error.URLError("refused")
    with pytest.raises(MailTransportError) as ei:
        mail_secret.smtp_password(transport=down)
    assert ei.value.kind == "connect" and ei.value.retryable


def test_the_relay_logs_in_with_the_brokers_value_and_never_prints_it(broker_env, relay, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "sender@vexa.test")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/smtp")
    b = _Broker()
    monkeypatch.setattr(mail_secret.urllib.request, "urlopen", b)
    conn, _ = emailx._smtp()
    assert conn.logins == [("sender@vexa.test", "app-pass")]
    assert conn.events[-1] == "login-encrypted"


def test_a_refused_brokered_password_is_read_afresh_next_time(broker_env, relay, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_Conn, "offers_starttls", True)
    monkeypatch.setenv("VEXA_MAIL_SMTP_USER", "sender@vexa.test")
    monkeypatch.setenv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/smtp")
    b = _Broker(store={"smtp": "wrong"})
    monkeypatch.setattr(mail_secret.urllib.request, "urlopen", b)
    with pytest.raises(MailTransportError) as ei:
        emailx._smtp()
    assert ei.value.kind == "auth" and "wrong" not in str(ei.value)
    b.store["smtp"] = "rotated"
    conn, _ = emailx._smtp()
    assert conn.logins[-1] == ("sender@vexa.test", "rotated")
