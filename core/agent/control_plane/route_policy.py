"""route_policy.py — which agent-api verbs need a person in the loop, read as data and enforced once.

The edge forwards this domain wholesale (`routes.v1.json` `forward`: public `/agent/<path>` is
agent-api's `/api/<path>`), so its `routes` rows are a handful of literals and the `{path:path}`
catch-all — they cannot say anything about one verb behind the forward. The manifest's `verbs` rows
do: each names one of those verbs by its public path and carries the policy the catch-all cannot.
Today that is one flag:

    {"method": "DELETE", "path": "/agent/workspace/{slug}", "person": true}

`person` — the verb needs a person in the loop. A worker dispatched without one (a delegated
identity whose regime is not `human`, an empty regime included; `ceiling.is_unwatched`) is refused
with the one `REFUSAL` body before the route runs. The edge does not register these rows: they are
agent-api's, and the catch-all already carries them.

ENFORCED IN ONE PLACE. `PERSON_GATE` is an app-level dependency (`create_app`), so it runs for the
route a request MATCHED, before the handler, and no route asks for it by hand. A verb is declared by
adding its row, never by remembering a call in its body.

FAILS CLOSED, at boot rather than on a request:

    a manifest with no `verbs` list                  ->  every verb would silently need nobody
    a row that is not {method, path, person: bool}   ->  a flag nobody can read the same way twice
    a row outside the forward's edge prefix          ->  a path that maps onto no agent-api route
    the same (method, path) twice                    ->  two rows, one of which is not read
    a row naming a route this app does not serve     ->  a typo that drops a verb's protection
                                                         (`assert_served`, run by `create_app`)
    a destructive or membership route with no flag   ->  a new verb that lands unprotected
                                                         (`assert_served`: any DELETE, and any
                                                         other write whose path has a
                                                         `MEMBERSHIP_SEGMENTS` segment)

DEFAULT-DENY FOR SHARED TOKENS (D3, 0.13.4). A worker whose turn other people read — a room's turn
or a shared chat's, its delegation carrying `scope.shared` (header `x-user-delegation-shared: 1`,
written by the identity door only from the signed claim) — reaches ONLY the routes this manifest
declares `"shared": "allow"`. Every `routes` row and every `verbs` row carries the declaration, and
every route this app serves under the forward has one of the two (`assert_shared_declared`, run by
`create_app` and by `tests/test_shared_routes.py`). `SHARED_GATE` is an app-level dependency like
`PERSON_GATE`: it refuses such a worker, with the one `SHARED_REFUSAL` body, on every route whose
matched template is not declared `allow` — an undeclared route, a route this request matched nothing
on, and the catch-all included. Before D3 the refusal was opt-in, per router (`require_private`), so
every new person-wide route was a new leak (security review 2026-10-17, C1); now a new route is
refused to a shared token until someone writes `allow` beside it, and CI fails until someone writes
either. The same table filters the MCP tool manifest a shared caller is shown
(`routers/health.py` stamps each tool's `shared`; the MCP edge's `visibility.py` reads it) and the
worker's own allow-set (`worker/mcp_tools.v1.json` `shared_tools`, held equal by the test).

    a row or verb without `shared`, or not allow|refuse  ->  a reach nobody can read the same way
                                                            twice (`shared_allowed`, at import)
    a served route under the forward with no declaration ->  a new route that lands reachable
                                                            (`assert_shared_declared`)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import FrozenSet, Iterable, Optional, Tuple

from fastapi import Depends, Request

from control_plane.ceiling import refused, require_person
from control_plane import identity_token

#: `core/agent/routes.v1.json` — `parents[1]` from this file (control_plane/ -> core/agent/). The
#: agent-api image COPYs it beside `mcp.tools.v1.json` (F221).
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "routes.v1.json"
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

RouteKey = Tuple[str, str]


#: A non-GET route whose path has one of these segments changes who a workspace is shared with, or
#: what credential reaches it, and must be flagged — a new one included, the day it lands.
MEMBERSHIP_SEGMENTS = frozenset({"members", "invites", "invite", "membership", "leave", "unshare",
                                 "share-enable", "archive", "deploy-key", "git-token"})
#: Destructive routes a delegated caller can never reach, so the flag is not theirs: the internal
#: tier only (a worker never holds it).
NOT_A_WORKER_DOOR = frozenset({("POST", "/api/workspace/git/reset"),
                               # the terminal's join POST spends a mailed invite's sign-in half (S2);
                               # it answers only X-Internal-Secret, which no worker holds
                               ("POST", "/internal/invites/signin-claim")})


class PolicyError(Exception):
    """A `verbs` declaration agent-api must not boot with."""


def destructive_family(keys: Iterable[RouteKey]) -> FrozenSet[RouteKey]:
    """The keys that must carry `person`: every DELETE, and every other write whose path has a
    `MEMBERSHIP_SEGMENTS` segment — less the internal-only `NOT_A_WORKER_DOOR`."""
    return frozenset((m, p) for m, p in keys
                     if m not in ("GET", "HEAD")
                     and (m == "DELETE" or MEMBERSHIP_SEGMENTS & set(p.split("/")))) - NOT_A_WORKER_DOOR


def person_verbs(doc: dict) -> FrozenSet[RouteKey]:
    """The agent-api ``(method, route template)`` keys whose `verbs` row says `person: true`, mapped
    from the public path through the manifest's `forward`. `PolicyError` on a malformed declaration."""
    forward = doc.get("forward") or {}
    edge, upstream = forward.get("edge_prefix"), forward.get("upstream_prefix")
    if not (isinstance(edge, str) and isinstance(upstream, str) and edge and upstream):
        raise PolicyError("routes.v1 declares no forward, so its verbs map onto no agent-api route")
    rows = doc.get("verbs")
    if not isinstance(rows, list):
        raise PolicyError("routes.v1 has no `verbs` list — which verbs need a person is declared there")
    seen: set = set()
    out: set = set()
    for row in rows:
        if not isinstance(row, dict):
            raise PolicyError(f"a verbs row must be an object (got {row!r})")
        method = str(row.get("method") or "").upper()
        path = str(row.get("path") or "")
        person = row.get("person")
        if method not in METHODS:
            raise PolicyError(f"verbs: {method!r} is not an HTTP method ({path})")
        if not path.startswith(edge) or path == edge:
            raise PolicyError(f"verbs: {method} {path} is outside the forward ({edge}…)")
        if not isinstance(person, bool):
            raise PolicyError(f"verbs: {method} {path} — person must be true or false (got {person!r})")
        key = (method, upstream + path[len(edge):])
        if key in seen:
            raise PolicyError(f"verbs: {method} {path} is declared twice")
        seen.add(key)
        if person:
            out.add(key)
    return frozenset(out)


def load(path: Optional[Path] = None) -> FrozenSet[RouteKey]:
    """`person_verbs` of the committed manifest. A missing or unreadable file is a `PolicyError`."""
    p = path or MANIFEST_PATH
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise PolicyError(f"{p} is not readable: {e}") from e
    return person_verbs(doc)


PERSON_VERBS: FrozenSet[RouteKey] = load()

SHARED_VALUES = frozenset({"allow", "refuse"})


def shared_table(doc: dict) -> "dict[RouteKey, str]":
    """Every ``(method, agent-api route template)`` the manifest declares, mapped to its `shared`
    value — the `routes` rows (the catch-all excluded: it names no route) and the `verbs` rows,
    through the forward. `PolicyError` when any row or verb lacks the declaration or names a value
    other than allow|refuse, or when a row and a verb disagree about one route."""
    forward = doc.get("forward") or {}
    edge, upstream = forward.get("edge_prefix"), forward.get("upstream_prefix")
    if not (isinstance(edge, str) and isinstance(upstream, str) and edge and upstream):
        raise PolicyError("routes.v1 declares no forward, so its shared rows map onto no agent-api route")
    out: dict = {}
    for kind in ("routes", "verbs"):
        for row in doc.get(kind) or []:
            method = str(row.get("method") or "").upper()
            path = str(row.get("path") or "")
            value = row.get("shared")
            if value not in SHARED_VALUES:
                raise PolicyError(f"{kind}: {method} {path} — shared must be allow or refuse (got {value!r}); "
                                  "every row declares whether a shared (room) token may reach it (D3)")
            if not path.startswith(edge) or path.endswith("{path:path}"):
                continue
            key = (method, upstream + path[len(edge):])
            if key in out and out[key] != value:
                raise PolicyError(f"{method} {path}: a routes row and a verbs row disagree about shared")
            out[key] = value
    return out


def load_shared(path: Optional[Path] = None) -> "dict[RouteKey, str]":
    """`shared_table` of the committed manifest. A missing or unreadable file is a `PolicyError`."""
    p = path or MANIFEST_PATH
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise PolicyError(f"{p} is not readable: {e}") from e
    return shared_table(doc)


SHARED_TABLE: "dict[RouteKey, str]" = load_shared()
#: The agent-api routes a shared (room / shared-chat) token may reach. Everything else refuses it.
SHARED_ALLOWED: FrozenSet[RouteKey] = frozenset(k for k, v in SHARED_TABLE.items() if v == "allow")
#: The forward's upstream prefix (`/api/`): the routes a worker can reach through the edge at all.
#: `shared_table` already refused a manifest whose forward is not this shape.
UPSTREAM_PREFIX = "/api/"


def _is_shared_caller(request: Request) -> bool:
    """A shared turn: the signed delegation says `shared`, or the caller is a WORKSPACE principal
    (D2, `x-user-principal-kind: workspace`) — which is shared by construction (the minter refuses one without `shared`), so
    a header that went missing never reads as private."""
    if identity_token.is_shared(request.headers):
        return True
    return identity_token.is_workspace_principal(request.headers)


def shared_gate(request: Request) -> None:
    """Refuse a shared (room / shared-chat) worker on every route the manifest does not declare
    `shared: allow`. A request whose matched route cannot be read is refused: the fail direction is
    closed. Routes outside the forward (`/internal/*`, `/invocations`, `/health`) are not a
    worker's door — the edge maps only `/agent/*` onto `/api/*` — and are left to their own tier."""
    if not _is_shared_caller(request):
        return
    key = _matched(request)
    if key is not None and not key[1].startswith(UPSTREAM_PREFIX):
        return
    if key is None or key not in SHARED_ALLOWED:
        raise refused(request, 403, identity_token.SHARED_REFUSAL,
                      reason=identity_token.SHARED_REFUSAL["reason"])


#: The dependency `create_app` installs on the whole app, beside `PERSON_GATE`.
SHARED_GATE = Depends(shared_gate)


def _matched(request: Request) -> Optional[RouteKey]:
    """(method, route TEMPLATE) of the route this request matched."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return (request.method.upper(), path) if path else None


def person_gate(request: Request) -> None:
    """Refuse an unwatched worker on a verb declared `person`. A request whose matched route cannot
    be read is treated as one: the fail direction is closed."""
    key = _matched(request)
    if key is None or key in PERSON_VERBS:
        require_person(request)


#: The dependency `create_app` installs on the whole app, and a test app that mounts one router.
PERSON_GATE = Depends(person_gate)


def served_keys(routes: Iterable) -> FrozenSet[RouteKey]:
    """Every (method, template) the given routes serve, an included router's own routes included."""
    out: set = set()
    for r in routes:
        inc = getattr(r, "include_context", None)
        if inc is not None:
            out |= served_keys(r.original_router.routes)
        elif getattr(r, "path", None) and getattr(r, "methods", None):
            out |= {(m, r.path) for m in r.methods}
    return frozenset(out)


def assert_shared_declared(routes: Iterable, table: "Optional[dict]" = None) -> None:
    """Every route this app serves under the forward declares whether a shared token may reach it
    — or the boot is refused, naming them (D3). The gate refuses an undeclared route anyway; this is
    what makes the omission loud the day the route lands instead of the day a room needs it."""
    table = SHARED_TABLE if table is None else table
    served = {k for k in served_keys(routes) if k[1].startswith(UPSTREAM_PREFIX) and k[0] != "HEAD"}
    undeclared = sorted(served - set(table))
    if undeclared:
        raise PolicyError("agent-api serves routes routes.v1 does not declare `shared: allow|refuse` "
                          "(D3: add a verbs row for each): "
                          + ", ".join(f"{m} {p}" for m, p in undeclared))


def assert_served(routes: Iterable) -> None:
    """Every declared person verb names a route this app serves, and every destructive or
    membership route this app serves is declared one — or the boot is refused, naming them. A
    renamed route would otherwise leave its row matching nothing, and a new destructive route would
    land with no row at all; either way the verb would be unprotected."""
    served = served_keys(routes)
    missing = sorted(PERSON_VERBS - served)
    if missing:
        raise PolicyError("routes.v1 verbs name routes agent-api does not serve: "
                          + ", ".join(f"{m} {p}" for m, p in missing))
    unflagged = sorted(destructive_family(served) - PERSON_VERBS)
    if unflagged:
        raise PolicyError("agent-api serves destructive or membership routes routes.v1 does not "
                          "flag `person: true`: " + ", ".join(f"{m} {p}" for m, p in unflagged))
