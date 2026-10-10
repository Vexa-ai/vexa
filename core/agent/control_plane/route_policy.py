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
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import FrozenSet, Iterable, Optional, Tuple

from fastapi import Depends, Request

from control_plane.ceiling import require_person

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
NOT_A_WORKER_DOOR = frozenset({("POST", "/api/workspace/git/reset")})


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
