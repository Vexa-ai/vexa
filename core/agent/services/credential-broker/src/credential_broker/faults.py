"""An upstream the broker calls on a person's behalf failed in a way the person cannot fix.

Two kinds of failure leave the adapters (`providers.py` for Google, `secret_service.py` and
`service_oauth.py` for a custom service), and they must never be confused:

* a REFUSAL — authorization rejected, a permission not granted, arguments the upstream rejected —
  is `providers.ProviderError` / `secret_service.ServiceError`, answered 409 with a sentence that
  tells the person what to do (often: reconnect);
* a FAULT — the upstream could not be reached, rate-limited us, or answered with something
  unusable — is `UpstreamFault`, answered 503 (`unreachable`, `rate_limited`) or 502
  (`bad_answer`) and logged as a `broker_fault` line with its source and kind. Reconnecting does
  not fix an outage, so a fault's sentence never says to.
"""
from __future__ import annotations

#: kind -> the HTTP status the broker answers with.
STATUS = {"unreachable": 503, "rate_limited": 503, "bad_answer": 502}


class UpstreamFault(Exception):
    """``source`` is ``provider`` (Google) or ``service`` (a custom service); ``kind`` is one of
    :data:`STATUS`. The message is a fixed sentence: never a response body, a token or a secret."""

    def __init__(self, source: str, kind: str, message: str) -> None:
        if kind not in STATUS:
            raise ValueError(f"unknown upstream fault kind {kind!r}")
        super().__init__(message)
        self.source = source
        self.kind = kind
        self.status = STATUS[kind]
