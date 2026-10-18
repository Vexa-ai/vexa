"""privacy_label.py — meeting-api's answers say whether they carry the person's meeting data (D6).

The gateway marks a worker's session from ``X-Vexa-Privacy`` on what it read (`core/agent/shared/
taint.py`); an answer with no label is private there. Transcripts, meetings and their participants,
recordings and a bot's in-call chat are ``private; class=transcript``. Everything else here is an act
on a bot or an account (``none``).
"""
from __future__ import annotations

import re

HEADER = b"x-vexa-privacy"
PRIVATE = b"private; class=transcript"
NONE = b"none"
_PRIVATE = re.compile(r"^/(transcripts|meetings|recordings)(/|$)|^/bots/[^/]+/[^/]+/chat$")


def label_for(path: str) -> bytes:
    return PRIVATE if _PRIVATE.search(path or "") else NONE


class PrivacyLabel:
    """ASGI: stamp the label on every HTTP response that does not carry one already."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        value = label_for(scope.get("path", ""))

        async def stamped(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(k.lower() == HEADER for k, _ in headers):
                    headers.append((HEADER, value))
                    message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, stamped)
