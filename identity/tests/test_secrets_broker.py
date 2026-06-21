"""SecretsPort broker evals (P15) — scoped credential, audited, raw value NEVER logged.

Asserts: the broker returns a scoped BrokeredSecret; the raw value is reachable only via reveal();
the value never appears in repr/str/format, in captured logs, or in the audit trail.
"""

import logging

import pytest

from identity_core import (
    AuditEvent,
    BrokeredSecret,
    PassthroughSecretsBroker,
    SecretsPort,
)

RAW = "ghp_SUPERSECRETtoken1234567890"


@pytest.fixture
def broker():
    b = PassthroughSecretsBroker()
    b.put("github_token", RAW)
    return b


def test_broker_returns_scoped_credential(broker):
    """get_secret hands back a BrokeredSecret carrying the scope + subject; reveal() yields raw."""
    secret = broker.get_secret("agent-42", "github_token", scope="repo:read")
    assert isinstance(secret, BrokeredSecret)
    assert secret.scope == "repo:read"
    assert secret.subject == "agent-42"
    assert secret.name == "github_token"
    assert secret.reveal() == RAW


def test_passthrough_broker_satisfies_port(broker):
    assert isinstance(broker, SecretsPort)


def test_raw_value_never_in_repr_str_or_format(broker):
    """repr / str / f-string interpolation must all be redacted — no accidental leak."""
    secret = broker.get_secret("agent-42", "github_token", scope="repo:read")
    assert RAW not in repr(secret)
    assert RAW not in str(secret)
    assert RAW not in f"{secret}"
    assert "REDACTED" in repr(secret)


def test_raw_value_never_logged(broker, caplog):
    """Brokering emits only metadata — the raw value must not appear in any log record."""
    with caplog.at_level(logging.DEBUG, logger="identity.secrets"):
        secret = broker.get_secret("agent-42", "github_token", scope="repo:read")
        # Even if a caller fumbles and logs the object, repr is redacted.
        logging.getLogger("identity.secrets").info("got %s", secret)
    full_log = caplog.text
    assert RAW not in full_log
    assert "github_token" in full_log  # metadata IS recorded
    assert "agent-42" in full_log


def test_audit_trail_records_metadata_not_value(broker):
    """The audit log captures who/what/when/decision but never the secret bytes."""
    broker.get_secret("agent-42", "github_token", scope="repo:read")
    audit = broker.audit_log
    assert len(audit) == 1
    ev = audit[0]
    assert isinstance(ev, AuditEvent)
    assert ev.subject == "agent-42"
    assert ev.secret_name == "github_token"
    assert ev.scope == "repo:read"
    assert ev.decision == "granted"
    # The value appears nowhere in the audit event's serialization.
    assert RAW not in str(ev)
    assert RAW not in repr(ev)


def test_denied_lookup_is_audited_without_value(broker):
    """A miss is audited as 'denied' and raises — still no value anywhere."""
    with pytest.raises(KeyError):
        broker.get_secret("agent-42", "missing_secret", scope="repo:read")
    audit = broker.audit_log
    assert audit[-1].decision == "denied"
    assert audit[-1].secret_name == "missing_secret"


def test_audit_log_is_a_copy(broker):
    """audit_log returns a snapshot — callers can't mutate the broker's internal trail."""
    broker.get_secret("agent-42", "github_token", scope="repo:read")
    snap = broker.audit_log
    snap.clear()
    assert len(broker.audit_log) == 1
