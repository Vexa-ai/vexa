"""RE-ENTRY — the identity the gateway signed onto a request, carried back on the tool call it causes.

A worker's delegation token (`vxd_…`) is admitted by the gateway on `/mcp` and nowhere else. The
tools behind this edge act by calling back into the gateway with the caller's own credential, so a
worker's tool call is a second request bearing that same token on a REST route — which the gateway
admits only when it can tell the request is this edge acting on an `/mcp` request it already let in.

What tells it is the identity the gateway itself signed onto that `/mcp` forward (`X-Vexa-Identity`,
gateway-identity.v1): only the gateway can make one, it lives a minute, and it names the person and
the delegation. This edge holds it for the life of the request (`ReentryMiddleware`, through the
in-process tool call `FastApiMCP` makes with the header forwarded) and hands it back to the gateway,
and only to the gateway, as `X-Vexa-Internal-Mcp-Identity` (`headers()`). The gateway verifies it
with its own key and strips it before anything behind the gateway sees it.

Nothing here verifies or reads the token: this edge holds no key and makes no decision. A request
that reached this service without passing the gateway has no token the gateway will accept.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Dict

#: Where the gateway puts the identity it signed (gateway-identity.v1's `HEADER`).
INBOUND_HEADER = "x-vexa-identity"
#: Where the gateway looks for it on a tool's call back into the edge.
OUTBOUND_HEADER = "X-Vexa-Internal-Mcp-Identity"

_current: ContextVar[str] = ContextVar("vexa_mcp_signed_identity", default="")
_INBOUND = INBOUND_HEADER.encode("latin-1")


class ReentryMiddleware:
    """ASGI: hold this request's signed identity (if any) for the code that serves it."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        token = ""
        for name, value in scope.get("headers") or ():
            if name.lower() == _INBOUND:
                token = value.decode("latin-1").strip()
                break
        held = _current.set(token)
        try:
            return await self.app(scope, receive, send)
        finally:
            _current.reset(held)


def headers() -> Dict[str, str]:
    """The re-entry header for a call to the GATEWAY, or nothing when this request carried none."""
    token = _current.get()
    return {OUTBOUND_HEADER: token} if token else {}
