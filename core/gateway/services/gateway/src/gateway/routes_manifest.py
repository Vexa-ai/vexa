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
    core/agent/routes.v1.json                       8   ← absent in the no-agents profile
    core/gateway/services/gateway/routes.v1.json    2   the edge's OWN /health and /auth/me

WHAT THIS MODULE REFUSES, and why each is a boot failure rather than a log line — every one of them
is otherwise found by a person hitting a route that answers wrongly:

    a manifest that is not routes.v1        ->  a file shape nobody validated, silently ignored
    the same (method, path) twice           ->  two domains claiming one route; last-one-wins
    a scope name outside the vocabulary     ->  a typo'd scope is an empty set is a DENY-ALL
    a manifest for an absent domain         ->  the table describing a service that is not there

A ROW CARRIES ITS POLICY, NOT ONLY ITS SCOPES. `"delegation": true` admits a worker's own
delegation token on that row (the MCP front door, the agent's friction report); every other row
refuses it. A domain the edge forwards wholesale declares `"forward": {"edge_prefix",
"upstream_prefix"}` once, and its rows are then only literals under the edge prefix (`"stream": true`
for a server-sent-event relay) or the prefix's `{path:path}` catch-all — so the edge registers that
domain from its manifest and names none of its routes.

    delegation that is not a boolean        ->  a flag nobody can read the same way twice
    delegation on an unscoped row           ->  a promise the authorizer never sees
    a forward with a malformed prefix       ->  a mapping that rewrites paths nobody declared
    a forwarded row outside its forward     ->  a route the edge would have to serve by name

DENY-BY-DEFAULT SURVIVES THE MOVE. The assembled table is still exhaustive and still enforced as
such by `create_app`: a registered route that no manifest declares refuses to build. What changed
is only WHO writes the declaration down.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, Optional, Set, Tuple

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
    #: the rows that admit a worker's own delegation token (`"delegation": true`).
    delegation: Set[RouteKey] = field(default_factory=set)
    #: the literal rows relayed as server-sent events (`"stream": true`).
    stream: Set[RouteKey] = field(default_factory=set)
    #: domain -> (edge_prefix, upstream_prefix), for each domain forwarded wholesale.
    forwards: Dict[str, Tuple[str, str]] = field(default_factory=dict)


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


CATCH_ALL = "{path:path}"


def _prefix(value) -> Optional[str]:
    """An absolute path prefix that ends at a segment boundary, or None."""
    if isinstance(value, str) and value.startswith("/") and value.endswith("/") and "{" not in value:
        return value
    return None


def _forward(domain: str, doc: dict) -> Optional[Tuple[str, str]]:
    raw = doc.get("forward")
    if raw is None:
        return None
    edge = _prefix(raw.get("edge_prefix")) if isinstance(raw, dict) else None
    upstream = _prefix(raw.get("upstream_prefix")) if isinstance(raw, dict) else None
    if not (edge and upstream):
        raise ManifestError(
            f"{domain}: forward must be {{\"edge_prefix\": \"/x/\", \"upstream_prefix\": \"/y/\"}} — two "
            f"absolute prefixes that end at a segment (got {raw!r})")
    return edge, upstream


def _flag(domain: str, row: dict, name: str) -> bool:
    value = row.get(name, False)
    if not isinstance(value, bool):
        raise ManifestError(f"{domain}: {row.get('method')} {row.get('path')} — {name} must be "
                            f"true or false (got {value!r})")
    return value


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
        forward = _forward(domain, doc)
        if forward:
            out.forwards[domain] = forward
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
            if _flag(domain, row, "delegation"):
                if not scopes:
                    raise ManifestError(
                        f"{domain}: {method} {path} is unscoped and cannot admit a delegation — an "
                        "unscoped row never reaches the authorizer that reads the flag")
                out.delegation.add(key)
            stream = _flag(domain, row, "stream")
            if forward:
                tail = path[len(forward[0]):] if path.startswith(forward[0]) else None
                if tail is None or not tail or ("{" in tail and tail != CATCH_ALL):
                    raise ManifestError(
                        f"{domain}: {method} {path} is outside its forward ({forward[0]}…): a "
                        f"forwarded row is a literal under the prefix or {forward[0]}{CATCH_ALL}")
                if stream and tail == CATCH_ALL:
                    raise ManifestError(f"{domain}: {method} {path} — only a literal row streams")
            elif stream:
                raise ManifestError(f"{domain}: {method} {path} — stream applies to a forwarded "
                                    "domain's literal rows only")
            if stream:
                out.stream.add(key)
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


def carried(candidates: Iterable[str], *, repo_root: Optional[pathlib.Path] = None) -> FrozenSet[str]:
    """Which of `candidates` this CUT actually ships a manifest for.

    Not every checkout holds every domain. The open-core cut is generated by dropping the agent
    surface, so `core/agent/routes.v1.json` is simply not on disk. That is a fact about the
    PRODUCT — the same fact as a deployment leaving `AGENT_API_URL` unset, one layer earlier.

    A MISSING FILE is a cut. A NAME with no entry in `manifest_paths` is a misspelling, and it
    still refuses: tolerating it would let one typo silently shrink the published table, with
    nothing anywhere to read.
    """
    root = repo_root or _repo_root()
    paths = manifest_paths(root)
    unknown = sorted(set(candidates) - set(paths))
    if unknown:
        raise ManifestError(f"no routes.v1 manifest is known for domain(s) {unknown}")
    return frozenset(d for d in candidates if paths[d].exists())


def load_carried(candidates: Iterable[str], *, repo_root: Optional[pathlib.Path] = None) -> Assembly:
    """Assemble over the candidates this cut CARRIES — an absent manifest is not an error here.

    THIS IS NOT A RELAXATION OF `load`, AND THE DIFFERENCE IS WHO IS ASKING.

    `load` is called by `create_app` with the domains a running deployment has NAMED. That table
    is a PROMISE, and a named domain whose manifest is missing is a real fault: the deployment
    asserted a door it cannot describe, would register the routes, and would serve rows no
    manifest scopes — the deny-by-default hole this module exists to close. It still refuses.

    This function is called once at import, to publish CANDIDATES: "what would a complete
    deployment of THIS cut serve". There, a domain the cut does not ship is not a fault, it is
    the cut — and importing a package must not hard-require a file from a domain the product
    does not include. Module scope asserting all five were on disk is what made `import gateway`
    raise in the open-core cut and take every test module in the gateway conformance package
    down at COLLECTION: an import-time hard requirement on optional substrate, the same class as
    the import-time prompts read that decision 18 removed.

    A quietly absent manifest cannot widen anything. It can only make the published table
    describe a smaller product — which is what a smaller product should publish.
    """
    root = repo_root or _repo_root()
    return load(carried(candidates, repo_root=root), repo_root=root)


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
