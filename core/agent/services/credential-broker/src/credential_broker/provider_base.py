"""What every provider adapter shares: the refusal a person can act on, the upstream-fault mapping,
the bearer header, the single-address check, and the Google scopes the routes test a grant for.

Split out of `providers.py` so the calendar-change adapter (`calendar_changes.py`) and the rest of
the adapters import one definition; `providers` re-exports every name here.
"""
from __future__ import annotations

import httpx

from .faults import UpstreamFault

DRAFT_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
#: Google's scope for creating, changing and deleting events on the person's calendars.
CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"


class ProviderError(Exception):
    """A sanitized refusal the person can act on; never retains a provider response body."""


#: httpx failures where no answer arrived at all: the provider is unreachable, not refusing.
_NO_ANSWER = (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError)


def _fault_for_status(status: int, message: str) -> UpstreamFault:
    """A non-refusal HTTP answer: 429 is a rate limit, anything else (5xx, unexpected) a bad answer."""
    return UpstreamFault("provider", "rate_limited" if status == 429 else "bad_answer", message)


def _bearer(value) -> dict:
    return {"Authorization": "Bearer " + value["access_token"]}


def _address(addr: str) -> str:
    """One bare address, or ProviderError. No display names, no lists, no header breaks."""
    from email.utils import parseaddr

    if any(c in addr for c in "\r\n,;") or parseaddr(addr)[1] != addr or addr.count("@") != 1 \
            or not addr.split("@")[0] or "." not in addr.split("@")[1]:
        raise ProviderError("Use valid single email addresses (no names, lists or line breaks)")
    return addr
