"""agent_admission.py — who this install serves the agent tier to (``VEXA_AGENT_ALLOW``).

Read per request, after the identity door (``identity_token.IdentityGuard``) has rebuilt the
``x-user-*`` headers from the gateway's signature:

* ``VEXA_AGENT_ALLOW`` unset or empty — no restriction: every signed person is served (the
  default, so an install that never sets it behaves exactly as before).
* ``*`` — everyone.
* otherwise a comma list of user ids and/or email addresses, case-insensitive; an ``@domain``
  entry admits every address at that domain. A person not on the list gets
  ``403 {"detail": "agent_tier_not_enabled"}`` (a websocket closes 4403).

Not subject to the list: a request that names nobody (health, readiness, the runtime's callbacks
that authenticate themselves), and the internal tier (a correct ``X-Internal-Secret`` — flows,
agent-api's own service calls). A worker's delegated token names the person it acts for as its
subject, so it is admitted exactly when that person is.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Callable, Optional

log = logging.getLogger(__name__)

ENV = "VEXA_AGENT_ALLOW"
REFUSAL = "agent_tier_not_enabled"
#: Probes a load balancer or an operator calls; served whoever asks.
EXEMPT_PATHS = frozenset({"/health", "/healthz", "/ready", "/readyz", "/api/version"})


def parse(raw: Optional[str]) -> Optional[frozenset]:
    """The allow-list, lower-cased; ``None`` means no restriction (unset, empty, or ``*``)."""
    entries = [e.strip().lower() for e in (raw or "").split(",") if e.strip()]
    if not entries or "*" in entries:
        return None
    return frozenset(entries)


def admitted(allow: Optional[frozenset], user_id: str, email: str) -> bool:
    """Whether a person (``user_id``, ``email``) is on ``allow``."""
    if allow is None:
        return True
    uid = (user_id or "").strip().lower()
    mail = (email or "").strip().lower()
    if uid and uid in allow:
        return True
    if mail:
        if mail in allow:
            return True
        at = mail.rfind("@")
        if at >= 0 and mail[at:] in allow:
            return True
    return False


class AgentAdmission:
    """ASGI middleware: refuses the agent tier to a signed person ``VEXA_AGENT_ALLOW`` does not
    name. Sits directly inside the identity door, so the headers it reads are the signed ones."""

    def __init__(self, app, *, internal_secret: str = "",
                 env: Optional[Callable[[], Optional[str]]] = None) -> None:
        self.app = app
        self._internal = internal_secret or ""
        self._env = env or (lambda: os.environ.get("VEXA_AGENT_ALLOW"))

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        allow = parse(self._env())
        if allow is None or scope.get("path", "") in EXEMPT_PATHS:
            return await self.app(scope, receive, send)
        user_id = email = internal = ""
        for name, value in scope.get("headers") or []:
            n = name.decode("latin-1").lower()
            if n == "x-user-id":
                user_id = value.decode("latin-1")
            elif n == "x-user-email":
                email = value.decode("latin-1")
            elif n == "x-internal-secret":
                internal = value.decode("latin-1")
        if not user_id.strip():
            return await self.app(scope, receive, send)
        if self._internal and internal and hmac.compare_digest(
                internal.encode("latin-1"), self._internal.encode("utf-8")):
            return await self.app(scope, receive, send)
        if admitted(allow, user_id, email):
            return await self.app(scope, receive, send)
        log.info("agent tier refused for a person not on %s: %s %s", ENV,
                 scope.get("method", "WS"), scope.get("path", ""))
        if scope.get("type") == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 4403})
            return
        body = json.dumps({"detail": REFUSAL}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
