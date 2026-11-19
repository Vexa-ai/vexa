"""privacy_label.py — agent-api's own reads say whether what they returned is the person's private data
(D6, `shared/taint.py`).

Every agent-api response carries ``X-Vexa-Privacy``. The gateway reads it off a worker's response and
marks the session; a response with no label is treated there as private, so this table only has to
be RIGHT about what is NOT private — and when in doubt a route belongs above the line.

Private, by class (the person-wide routes and the person's sources): connections (mail, calendar, a
connected service), onboarding research, chat search and turns, notifications, home (history, the
customer profile), people, claims, budgets and usage, workspace states, reference resolution (a ref
may name a transcript or a CRM record) and meeting term scans (transcripts). Everything else agent-api
serves is about a workspace the caller already reaches, or is an act, and answers ``none``.

S1-B's route table (`routes.v1.json` ``shared: refuse``) names the same person-wide set from the other
side; when it lands, a route that refuses a shared token is private here by construction.
"""
from __future__ import annotations

import re

from shared.taint import HEADER, format_label

#: (path regex on agent-api's /api/ path, class). First match wins.
PRIVATE_ROUTES: tuple[tuple[re.Pattern, str], ...] = tuple((re.compile(p), c) for p, c in (
    (r"^/api/connections/gmail(/|$)", "mail"),
    (r"^/api/connections/calendar(/|$)", "calendar"),
    (r"^/api/connections/service(/|$)", "connected service"),
    (r"^/api/connections(/|$)", "connections"),
    (r"^/api/onboarding/research$", "mail"),
    (r"^/api/chats?/(search|read)(/|$)", "chat"),
    (r"^/api/chats/[^/]+/turns$", "chat"),
    (r"^/api/notifications$", "notifications"),
    (r"^/api/home(/|$)", "person"),
    (r"^/api/people(/|$)", "person"),
    (r"^/api/claims$", "person"),
    (r"^/api/budgets(/|$)", "person"),
    (r"^/api/usage$", "person"),
    (r"^/api/workspace/states$", "person"),
    (r"^/api/refs/resolve$", "refs"),
    (r"^/api/meeting/terms/scan$", "transcript"),
    (r"^/api/held(/|$)", "held"),
))


def classify(method: str, path: str) -> set:
    """The private classes a route's answer carries (empty = none)."""
    for rx, cls in PRIVATE_ROUTES:
        if rx.search(path):
            return {cls}
    return set()


class PrivacyLabelMiddleware:
    """ASGI: stamp ``X-Vexa-Privacy`` on every HTTP response that does not already carry one."""

    def __init__(self, app) -> None:
        self.app = app
        self._name = HEADER.lower().encode("latin-1")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        value = format_label(classify(scope.get("method", "GET"), scope.get("path", ""))).encode("latin-1")

        async def stamped(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(k.lower() == self._name for k, _ in headers):
                    headers.append((self._name, value))
                    message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, stamped)
