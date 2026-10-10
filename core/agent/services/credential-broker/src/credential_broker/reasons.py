"""Why a credential operation did not happen, as one word a caller can act on (credential-broker.v1
``Error.reason``).

The sentence in ``detail`` is for a person; ``reason`` is for the program in between (agent-api, and
through it the agent and the Connections panel), which must tell three cases apart:

* ``store_unavailable`` — the credential store does not answer, or refuses this broker (an expired
  or revoked store token, a wrong key). An outage of this deployment: reconnecting cannot help,
  since a new authorization would be written to the same store.
* ``reconnect_required`` — the provider no longer accepts the stored authorization, or a permission
  is missing. The person reconnects in the Connections panel.
* ``provider_error`` — the provider or service refused the request for another reason, or is down
  (an ``UpstreamFault``). Reconnecting does not help.

A refusal's reason is decided from its sentence, and only the sentences listed in ``RECONNECT``
mean reconnect: every sentence this package raises that asks for a reconnect is listed here, and
``tests/test_error_reasons.py`` fails when one is added without being listed.
"""
from __future__ import annotations

from fastapi import HTTPException

REASONS = ("store_unavailable", "reconnect_required", "provider_error")

RECONNECT = frozenset({
    "Authorization failed; reconnect this account",
    "Required permission was not granted; reconnect this account",
    "Authorization rejected; reconnect this account",
    "Authorization expired; reconnect this account",
    "Required permissions were not granted; reconnect",
    "Authorization expired; reconnect",
})


def refusal_reason(sentence: str) -> str:
    """The reason for a 409 refusal raised with ``sentence`` (a fixed sentence of this package)."""
    return "reconnect_required" if sentence in RECONNECT else "provider_error"


class ReasonedError(HTTPException):
    """An HTTP answer that carries a ``reason`` beside its ``detail`` (``app.py`` renders both)."""

    def __init__(self, status_code: int, detail: str, reason: str) -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown reason {reason!r}")
        super().__init__(status_code, detail)
        self.reason = reason


def store_unavailable() -> ReasonedError:
    return ReasonedError(503, "Credential store unavailable", "store_unavailable")
