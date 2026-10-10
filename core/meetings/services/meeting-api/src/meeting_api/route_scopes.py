"""route_scopes.py — the key scopes each meeting-api route needs, read from the edge's own table.

The gateway checks a key's scopes against the meetings domain's rows (`core/meetings/routes.v1.json`)
and forwards each row to its own path on meeting-api, or to the `upstream` the row names. The signed
identity it stamps on the hop (gateway-identity.v1) carries the key's scopes as `x-user-scopes`. This
module checks them a second time, here, against the route the request actually MATCHED — so a hop
that reached a route other than the one the edge checked is refused by the route it landed on.

THE TABLE IS THE MANIFEST, not a copy of it. A meeting-api route needs the union of the scopes of the
rows that reach it: `POST /meetings/{meeting_id}/share` is reached by its own row and by the
`/transcripts/by-id/{meeting_id}/share` alias, both `tx`. Adding a row, or moving one, changes both
ends at once.

WHAT IS CHECKED, AND WHAT IS NOT. Only a request carrying the gateway's signed identity
(`x-vexa-identity`, verified by `identity_token.IdentityGuard` before any route runs). The internal
tier (`X-Internal-Secret`) is a service, not a key, and names no scopes; bot and runtime callbacks
carry no identity and authenticate themselves.

DENY BY DEFAULT. A signed identity on a route that no row reaches is refused, apart from the edge's
own hops (`EDGE_HOPS`), which carry the identity without being a row.

The rule is the edge's: a key passes when it holds ANY of the route's scopes
(`gateway/app.py _authorize`), and is refused with the edge's body when it holds none.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Tuple

from fastapi import Depends, HTTPException, Request

from . import identity_token

RouteKey = Tuple[str, str]

#: `core/meetings/routes.v1.json` in the repository; `meetings/routes.v1.json` under `/app` in the
#: meeting-api image (its Dockerfile COPYs it beside the sealed contracts this package reads the
#: same way), found by walking up from this file.
MANIFEST = Path("meetings") / "routes.v1.json"

#: Calls the gateway makes ITSELF with a signed identity, which are not routes it serves: the `/ws`
#: subscribe authorizes each meeting for whichever key opened the socket (`adapters.authorize_subscribe`).
EDGE_HOPS: FrozenSet[RouteKey] = frozenset({("POST", "/ws/authorize-subscribe")})

#: The answer the edge gives a key without the route's scope (`gateway/app.py`), so a client reads
#: one refusal whichever side refused it.
INSUFFICIENT_SCOPE = "Insufficient scope for this endpoint"


class ScopeTableError(Exception):
    """A meetings manifest this service must not boot with."""


def _find(rel: Path) -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return candidate
    raise ScopeTableError(f"the meetings route manifest is not found by path: {rel}")


def scopes_by_route(doc: dict) -> Dict[RouteKey, FrozenSet[str]]:
    """meeting-api `(method, route template)` -> the scopes of every row that reaches it."""
    if doc.get("contract") != "routes.v1" or doc.get("domain") != "meetings":
        raise ScopeTableError("the meetings route manifest is not a routes.v1 meetings manifest")
    rows = doc.get("routes")
    if not isinstance(rows, list) or not rows:
        raise ScopeTableError("the meetings route manifest declares no routes")
    out: Dict[RouteKey, set] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ScopeTableError(f"a routes row must be an object (got {row!r})")
        method = str(row.get("method") or "").upper()
        target = row.get("upstream", row.get("path"))
        scopes = row.get("scopes")
        if not method or not isinstance(target, str) or not target.startswith("/"):
            raise ScopeTableError(f"a routes row names no route: {row!r}")
        if not isinstance(scopes, list) or not scopes or not all(isinstance(s, str) for s in scopes):
            # Every meetings row is scoped; an unscoped one here would read as "any key" — refuse it
            # rather than guess.
            raise ScopeTableError(f"{method} {row.get('path')} declares no scopes")
        out.setdefault((method, target), set()).update(scopes)
    return {k: frozenset(v) for k, v in out.items()}


def load(path: Optional[Path] = None) -> Dict[RouteKey, FrozenSet[str]]:
    p = path or _find(MANIFEST)
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise ScopeTableError(f"{p} is not readable: {e}") from e
    return scopes_by_route(doc)


ROUTE_SCOPES: Dict[RouteKey, FrozenSet[str]] = load()


def _matched(request: Request) -> Optional[RouteKey]:
    path = getattr(request.scope.get("route"), "path", None)
    return (request.method.upper(), path) if path else None


def _refuse() -> HTTPException:
    return HTTPException(status_code=403, detail=INSUFFICIENT_SCOPE)


def scope_gate(request: Request) -> None:
    """Refuse a gateway-signed request whose key holds none of the matched route's scopes, and one
    on a route no meetings row reaches. A request without the signed identity is not checked here."""
    if identity_token.HEADER not in request.headers:
        return
    key = _matched(request)
    if key is None:
        raise _refuse()  # a matched route that cannot be read is treated as undeclared
    if key in EDGE_HOPS:
        return
    required = ROUTE_SCOPES.get(key)
    if required is None:
        raise _refuse()
    held = {s.strip() for s in (request.headers.get("x-user-scopes") or "").split(",") if s.strip()}
    if not held & required:
        raise _refuse()


#: The dependency `create_app` installs on the whole app.
SCOPE_GATE = Depends(scope_gate)
