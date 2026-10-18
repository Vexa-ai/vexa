"""delegation.py — where a worker's delegation token is admitted at this edge, and nowhere else.

A `vxd_` bearer is minted per dispatch for the worker's toolbelt, and its audience is the MCP. The
edge therefore admits it on `/mcp` and nowhere else, so what a worker can do is what the MCP's
tools do — not every REST route a person's own key reaches.

The MCP's tools act by calling back into this edge with the same bearer. That hop is admitted when
it carries, in `MCP_REENTRY_HEADER`, the identity this edge signed onto the `/mcp` forward it is
serving: only this edge can sign one, it lives a minute, and it must name the same person under
the same delegation as the bearer. The header is in the `x-vexa-internal-` family, so a client
cannot pass one through this edge to anything behind it.

AND ONLY ON A ROUTE THE MCP'S TOOLS CALL. A valid re-entry proves the MCP is acting on an `/mcp`
request this edge admitted; it does not prove the route is one an MCP tool calls. That second fact
is data — `"mcp_reentry": true` on the route's routes.v1 row (`routes_manifest.py`) — and
`create_app` admits re-entry only where both hold. A valid re-entry on any other row is refused
like any other delegated REST call.

`create_app` asks this module three questions: is this caller a worker (`is_delegated`), is this
request the MCP acting on an admitted `/mcp` request (`McpReentry.admits`), and what does
`/auth/me` say about a worker's admin standing (`reported_admin`). The `/ws` multiplex asks the
first one too.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Optional

from fastapi import Request, Response

from . import identity_token

MCP_REENTRY_HEADER = "x-vexa-internal-mcp-identity"
#: The prefix every delegation token carries — `PREFIX` in the identity domain's `delegation.py`
#: (admin-api and its agent-api twin), which mint and verify the token. Spelled here once, by name,
#: so the three sites can be held equal.
DELEGATION_TOKEN_PREFIX = "vxd_"


def is_delegated(client_key: str, user_data: Mapping) -> bool:
    """Is this caller a worker acting under a delegation? Identity's answer says so (`delegation`),
    and so does the token's own prefix: an identity answer that lost the ceiling cannot turn a
    worker's token into a person's key."""
    return (isinstance(user_data.get("delegation"), Mapping)
            or str(client_key).startswith(DELEGATION_TOKEN_PREFIX))


def delegated_route_response() -> Response:
    return Response(
        content=json.dumps({"detail": "a worker's delegation token is accepted on /mcp only"}),
        status_code=403,
        media_type="application/json",
    )


class McpReentry:
    """Recognises the MCP's own tool calls on behalf of an `/mcp` request this edge admitted."""

    def __init__(self, signing_key=None) -> None:
        # Verified with the public half of the key this edge signs identities with. No key (a
        # harness that forwards plain headers) means no request is ever re-entry.
        self._key = signing_key.public_key() if signing_key is not None else None

    def admits(self, request: Request, user_data: Mapping) -> bool:
        """True only for an identity this edge signed (verified with its own key's public half),
        still within its lifetime, naming the same person and the same delegation as the bearer
        resolved now. Anything else — no token, a forged or expired one, another person's, a
        person's own identity without the delegation — is not re-entry."""
        if self._key is None:
            return False
        token = (request.headers.get(MCP_REENTRY_HEADER) or "").strip()
        if not token:
            return False
        try:
            claims = identity_token.verify(self._key, token)
        except identity_token.IdentityError:
            return False
        want = identity_token.claims_from_validation(user_data)
        return (claims.get("sub") == want.get("sub")
                and isinstance(want.get("delegation"), dict)
                and claims.get("delegation") == want.get("delegation"))


def reported_admin(user_data: Mapping, *, delegated: bool) -> bool:
    """`/auth/me`'s `is_admin`, from identity's own answer (`/internal/validate`).

    The MCP edge asks it before it spends the deployment's operator key on an `auth: admin` tool.
    A person's key is the admin when identity says so. A worker is the admin only when the person
    it acts for is the instance admin AND that person is in the loop (regime `human`); an
    unwatched run never is, whoever it acts for."""
    if not delegated:
        return user_data.get("is_admin") is True
    dlg: Optional[Mapping] = user_data.get("delegation")
    if not isinstance(dlg, Mapping):
        return False
    return str(dlg.get("regime") or "") == "human" and user_data.get("person_is_admin") is True
