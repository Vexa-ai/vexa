"""SecretsPort — a credential broker that returns a scoped, audited secret and NEVER logs the raw
value (P15).

The seam: agents and services need short-lived, scoped credentials (e.g. the GitHub token for the
agent's workspace sync — see admin-api `workspace_git.token`). Rather than hand the raw value around
and risk it landing in a log line, callers ask the broker; it returns a `BrokeredSecret` whose
`repr`/`str` are redacted, while emitting an audit trail that records WHO got WHICH secret WHEN —
but never the secret bytes.

This file wires the PORT + a passthrough default adapter. The real vault-backed adapter (lease,
rotation, revocation) is deferred to P16; this keeps the contract stable so P16 swaps the adapter,
not the callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

logger = logging.getLogger("identity.secrets")

_REDACTED = "***REDACTED***"


@dataclass(frozen=True)
class AuditEvent:
    """One brokering event. Carries the metadata an auditor needs — NEVER the secret value."""

    subject: str
    secret_name: str
    scope: str
    granted_at: datetime
    decision: str  # "granted" | "denied"

    def __post_init__(self) -> None:
        # Defense in depth: an AuditEvent must never be constructed with the raw value as a field.
        # (There is intentionally no `value` field; this guards future edits.)
        assert not hasattr(self, "value"), "AuditEvent must not carry the secret value"


class BrokeredSecret:
    """A scoped credential whose value is reachable ONLY via `.reveal()`.

    `repr`/`str` are redacted so an accidental `logger.info(secret)` or f-string leaks nothing.
    The raw bytes live in a closure-free private attribute; everything else about the secret
    (name, scope, subject) is freely inspectable for audit/attribution.
    """

    __slots__ = ("_value", "name", "scope", "subject")

    def __init__(self, value: str, *, name: str, scope: str, subject: str) -> None:
        self._value = value
        self.name = name
        self.scope = scope
        self.subject = subject

    def reveal(self) -> str:
        """Explicitly read the raw value. The ONLY path to the secret bytes — grep-auditable."""
        return self._value

    def __repr__(self) -> str:
        return f"BrokeredSecret(name={self.name!r}, scope={self.scope!r}, subject={self.subject!r}, value={_REDACTED})"

    __str__ = __repr__

    # Block the common accidental-leak channels.
    def __format__(self, _spec: str) -> str:
        return _REDACTED


@runtime_checkable
class SecretsPort(Protocol):
    """The broker port. Brokers a scoped credential for a subject and records an audit trail."""

    def get_secret(self, subject: str, secret_name: str, *, scope: str) -> BrokeredSecret: ...

    @property
    def audit_log(self) -> list[AuditEvent]: ...


class PassthroughSecretsBroker:
    """Default adapter (P15): serves secrets from an in-memory store, audited, value never logged.

    A passthrough stand-in for the real vault (P16). It (a) returns a redacted `BrokeredSecret`,
    (b) appends an `AuditEvent` capturing subject/name/scope/time — but NOT the value, and
    (c) logs only metadata. Swap this adapter for the vault impl without touching callers.
    """

    def __init__(self, store: dict[str, str] | None = None) -> None:
        self._store: dict[str, str] = dict(store or {})
        self._audit: list[AuditEvent] = []

    def put(self, name: str, value: str) -> None:
        """Seed a secret (test/bootstrap helper). The value never leaves except via reveal()."""
        self._store[name] = value

    def get_secret(self, subject: str, secret_name: str, *, scope: str) -> BrokeredSecret:
        now = datetime.now(timezone.utc)
        if secret_name not in self._store:
            self._audit.append(AuditEvent(subject, secret_name, scope, now, "denied"))
            logger.info(
                "secret broker DENY subject=%s secret=%s scope=%s", subject, secret_name, scope
            )
            raise KeyError(f"No such secret: {secret_name!r}")

        self._audit.append(AuditEvent(subject, secret_name, scope, now, "granted"))
        # Metadata only — the value is never interpolated into the log record.
        logger.info(
            "secret broker GRANT subject=%s secret=%s scope=%s", subject, secret_name, scope
        )
        return BrokeredSecret(self._store[secret_name], name=secret_name, scope=scope, subject=subject)

    @property
    def audit_log(self) -> list[AuditEvent]:
        return list(self._audit)
