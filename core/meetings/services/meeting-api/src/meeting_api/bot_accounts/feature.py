"""feature.py — whether this install serves browser sessions and bot accounts (``VEXA_FEATURES``).

Bot accounts are built but not yet proven, so a release install leaves the feature ``browser_bots``
OFF: every public ``/bot-accounts…`` route then answers 403 with one typed body, ``feature_disabled``
(a socket is closed 4403 before it is accepted). The ``/internal/…`` routes a workload or a bot calls
are not the feature's door and stay served, so a profile already saved stays readable. Saved accounts
stay in the broker and are listed again when the feature is switched on.

The registry and the parse rules are agent-api's, ``core/agent/control_plane/features.py``
(``install_defaults``); a domain may not import another, so this is a copy of that parse for the one
name this service answers, held to it by ``tests/test_bot_accounts_feature.py``:

    empty or unset    every feature off (the release default)
    ``all``           every feature on (a dev stack)
    ``browser_bots``  just the named ones on (a comma list)
    ``all,-name``     a ``-name`` after ``all`` takes one back

Read from the process environment on every request. ``browser_bots`` is install-scope: no
company-layer policy switches it.
"""
from __future__ import annotations

import json
import os
import re
from typing import Mapping, Optional

ENV = "VEXA_FEATURES"
FEATURE = "browser_bots"
REASON = "feature_disabled"
SUMMARY = "browser sessions and bot accounts (sign a bot in once, bots join signed in)"
#: the public routes (router.py); ``/internal/bot-accounts/…`` does not match.
PUBLIC_ROUTES = re.compile(r"^/bot-accounts(/.*)?$")


def switched_on(name: str = FEATURE, env: Optional[Mapping[str, str]] = None) -> bool:
    """``name``'s answer from ``VEXA_FEATURES``, by agent-api's ``install_defaults`` rules."""
    raw = str((env if env is not None else os.environ).get(ENV) or "")
    on = False
    for token in (t.strip().lower() for t in raw.split(",")):
        if not token:
            continue
        value = not token.startswith("-")
        token = token.lstrip("-+").strip()
        if token in ("all", name):
            on = value
    return on


def refusal(name: str = FEATURE, summary: str = SUMMARY) -> dict:
    """The one body every switched-off route answers with (agent-api's ``features.refusal``)."""
    return {
        "status": "refused",
        "reason": REASON,
        "feature": name,
        "instruction": f"{name} is switched off on this deployment. Do not retry it another way; say "
                       f"it is not available here.",
        "tell_your_person": f"This isn't switched on here yet ({summary}). An administrator can turn it on.",
    }


class BrowserBotsGate:
    """ASGI middleware: a public bot-account request while ``browser_bots`` is off is refused before
    any route, identity check or relay runs."""

    def __init__(self, app, env: Optional[Mapping[str, str]] = None):
        self.app = app
        self.env = env

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind in ("http", "websocket") and PUBLIC_ROUTES.match(scope.get("path", "")) \
                and not switched_on(FEATURE, self.env):
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 4403, "reason": REASON})
                return
            body = json.dumps({"detail": refusal()}).encode()
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
