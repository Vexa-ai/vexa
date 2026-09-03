"""routes_manifest.py — the edge assembles its route table; it does not own one.

PRD decision 40.5: *"The gateway still owns nothing: it composes, strips authority, re-stamps,
forwards."* `ROUTE_SCOPES` was a 92-line literal in `app.py` holding all 69 rows — including the
agent domain's seven, which made the edge the place where a domain's route list was written down.
Decision 40.7 then makes that concretely wrong rather than merely untidy: **agents are optional**,
and a table that names `/agent/*` unconditionally cannot describe a deployment that has no agents.

So each domain declares its own routes beside its service, in a `routes.v1.json` — the same shape
as the `mcp.tools.v1` manifests and for the same reason: the domain that owns the door behind a
route is the only one that can say what the route is and what it costs.

    core/meetings/routes.v1.json                   38 rows
    core/identity/routes.v1.json                   12
    core/meetings/services/mcp/routes.v1.json      12
    core/agent/routes.v1.json                       7   ← absent in the no-agents profile
    core/gateway/services/gateway/routes.v1.json    2   the edge's OWN /health and /auth/me

WHAT THIS MODULE REFUSES, and why each is a boot failure rather than a log line — every one of them
is otherwise found by a person hitting a route that answers wrongly:

    a manifest that is not routes.v1        ->  a file shape nobody validated, silently ignored
    the same (method, path) twice           ->  two domains claiming one route; last-one-wins
    a scope name outside the vocabulary     ->  a typo'd scope is an empty set is a DENY-ALL
    a manifest for an absent domain         ->  the table describing a service that is not there

DENY-BY-DEFAULT SURVIVES THE MOVE. The assembled table is still exhaustive and still enforced as
such by `create_app`: a registered route that no manifest declares refuses to build. What changed
is only WHO writes the declaration down.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

CONTRACT = "routes.v1"
#: The scope vocabulary `docs/docs/authentication.mdx` defines. A manifest may not invent one: an
#: unknown scope name would be a set no key can satisfy, which reads as a deny and looks like a
#: policy decision somebody made on purpose.
SCOPES = frozenset({"bot", "tx", "browser"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})

RouteKey = Tuple[str, str]


class ManifestError(Exception):
    """A manifest, or a combination of them, this edge must not boot with."""


@dataclass
class Assembly:
    scopes: Dict[RouteKey, FrozenSet[str]] = field(default_factory=dict)
    unscoped: Set[RouteKey] = field(default_factory=set)
    domains: Dict[str, int] = field(default_factory=dict)          # domain -> rows contributed
    #: which domain declared each row, so a duplicate can name BOTH sides and an operator reading
    #: the refusal knows which two files to open.
    owner_of: Dict[RouteKey, str] = field(default_factory=dict)


def manifest_paths(repo_root: pathlib.Path) -> Dict[str, pathlib.Path]:
    """Where each domain's manifest lives. A map rather than a glob: a glob would silently pick up
    a manifest somebody dropped in a build directory, and silently MISS one whose service moved."""
    return {
        "gateway": repo_root / "core/gateway/services/gateway/routes.v1.json",
        "meetings": repo_root / "core/meetings/routes.v1.json",
        "identity": repo_root / "core/identity/routes.v1.json",
        "mcp": repo_root / "core/meetings/services/mcp/routes.v1.json",
        "agent": repo_root / "core/agent/routes.v1.json",
    }


def read(path: pathlib.Path) -> dict:
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError as e:
        raise ManifestError(f"{path} is missing — a deployed domain must declare its routes") from e
    except ValueError as e:
        raise ManifestError(f"{path} is not readable JSON: {e}") from e
    if doc.get("contract") != CONTRACT:
        raise ManifestError(f"{path} declares contract {doc.get('contract')!r}, not {CONTRACT!r}")
    if not doc.get("domain"):
        raise ManifestError(f"{path} names no domain")
    return doc


def assemble(manifests: Iterable[dict]) -> Assembly:
    """The union of what the DEPLOYED domains serve, or `ManifestError`.

    Absence is not an error and never reaches here: a domain that is not deployed contributes no
    manifest, so its routes are not in the table AND — because `create_app` registers a domain's
    routes on the same condition — not on the app either. That pairing is the whole point. A route
    present but unscoped would 403; a route absent answers **404**, which is the truth: this
    deployment does not serve it.
    """
    out = Assembly()
    for doc in manifests:
        domain = doc["domain"]
        rows = doc.get("routes") or []
        for row in rows:
            method = str(row.get("method", "")).upper()
            path = str(row.get("path", ""))
            if method not in METHODS:
                raise ManifestError(f"{domain}: {method!r} is not an HTTP method ({path})")
            if not path.startswith("/"):
                raise ManifestError(f"{domain}: {path!r} is not a route template")
            scopes = frozenset(row.get("scopes") or ())
            unknown = scopes - SCOPES
            if unknown:
                raise ManifestError(
                    f"{domain}: {method} {path} names scope(s) {sorted(unknown)} outside the "
                    f"vocabulary {sorted(SCOPES)} — an unknown scope is a set no key can hold, "
                    "which denies the route while reading like a decision")
            key = (method, path)
            if key in out.owner_of:
                raise ManifestError(
                    f"{method} {path} is declared by both {out.owner_of[key]!r} and {domain!r} — "
                    "one route, one owner; the edge composes and cannot arbitrate")
            out.owner_of[key] = domain
            if scopes:
                out.scopes[key] = scopes
            else:
                out.unscoped.add(key)
        out.domains[domain] = len(rows)
    return out


def load(present: Iterable[str], *, repo_root: Optional[pathlib.Path] = None) -> Assembly:
    """Assemble the table from the manifests of the domains this deployment runs."""
    root = repo_root or _repo_root()
    paths = manifest_paths(root)
    unknown = sorted(set(present) - set(paths))
    if unknown:
        raise ManifestError(f"no routes.v1 manifest is known for domain(s) {unknown}")
    return assemble(read(paths[d]) for d in sorted(present))


def _repo_root() -> pathlib.Path:
    """The carve root, found by walking up for a marker rather than counting `parents[n]`.

    Counted parents break silently when a package moves — the `core-mcp` split is moving one right
    now — and the breakage is an import error at boot in one deployment shape and not another."""
    here = pathlib.Path(__file__).resolve()
    for p in here.parents:
        if (p / "core" / "gateway").is_dir() and (p / "core" / "meetings").is_dir():
            return p
    raise ManifestError(
        "cannot find the repository root from "
        f"{here} — the gateway image must carry the routes.v1 manifests of the domains it fronts")
