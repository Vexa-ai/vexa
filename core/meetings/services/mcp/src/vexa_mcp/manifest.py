"""ASSEMBLY — one MCP server, built from the manifests of the domains that are deployed.

PRD decision 40, founder rulings of 2026-09-02. A tool belongs to the domain that owns the door
behind it: meetings owns the bots and the transcripts, identity owns the person, flows owns the
reaction engine, agent owns the desks. This service is an EDGE — like the gateway it carries the
caller's identity and holds no domain logic — and its whole job is to ask each deployed domain what
it serves, refuse the combinations that cannot be right, and present the union as one surface.

TWO SOURCES, ONE NAME SPACE. `oss` manifests come from the packages in this repo; `mounted` ones
come from a directory a private deployment supplies, so a paid domain composes onto this line
without a line of it living here. They are assembled identically and compete for names equally — a
mounted manifest gets no precedence, which is what makes a mount safe: it can COLLIDE, it can never
SHADOW.

EVERY RULE HERE FAILS THE BOOT. Not because failing loudly is a virtue in itself, but because each
of these failures is otherwise silent, and a silent one is discovered by a person asking for a tool
that is not there:

    a deployed domain that does not answer   ->  a whole domain's tools missing, quietly
    one name claimed twice                   ->  last-one-wins, and nobody knows which won
    a route the domain does not serve        ->  the manifest lying about its own service
    two entitlement hooks                    ->  "may this person act" answered twice
    a door this edge cannot authenticate     ->  a listed tool that 401s on first use

The one thing that is NOT a failure is a domain that is simply not deployed. Its tools are ABSENT
from `tools/list` — an agent that cannot see a tool recovers; an agent told a tool exists and then
handed a 502 tells the person the product is broken.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

CONTRACT = "mcp.tools.v1"

# PRD 40.8 — EXACTLY ONE AUTHENTICATION PATH into this edge: a bearer token in the header, the
# session bound by `Mcp-Session-Id`, and a token minted mid-conversation bound to that session.
# There is no `/do` GET bridge and no `token=` tool argument, because both put a credential in a
# query string or an argument list: right for a fetch-only agent on a private host, wrong anywhere
# requests are logged, and impossible to withdraw once a transcript holds it.
#
# It is enforced HERE, at the seam where a tool's surface is decided, rather than trusted to every
# domain: a tool's schema is derived from its route's OpenAPI operation, and a route has no `token`
# parameter — so the rule is mostly kept by construction. This list is what catches a domain that
# reintroduces one on purpose.
CREDENTIAL_ARGUMENTS = {"token", "api_key", "apikey", "key", "access_token", "bearer",
                        "credential", "password", "secret"}
# A DOMAIN NAME IS CHECKED FOR SHAPE, NOT FOR MEMBERSHIP. A closed list here had to spell every
# domain any deployment might ever mount, so this OSS module carried the names of surfaces this
# repository does not ship and cannot describe — a reader learns a private product's shape from a
# constant in the open assembler, and a private deployment cannot add a domain without a change
# landing here first. Neither is the mount's design: `load_mounted` exists so a paid domain composes
# onto this line without a line of it living here.
#
# What actually has to be true of a name is that it is a stable lowercase identifier, because the
# name is a key in three places at once — `depends_on`, every tool's `requires`, and the
# `<DOMAIN>_API_URL` env var discovery reads. Anything outside this pattern makes one of those
# unspellable, and that is the whole of the rule. Everything a manifest may CLAIM is still checked
# below; nothing here decides whether a domain is real, because `deployed` already does.
DOMAIN_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
IDENTITIES = {"user", "admin", "operator", "none"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# WHICH CREDENTIAL THE DOOR BEHIND A TOOL READS — issue #1468. `identity` above says who the CALLER
# must be; this says what THIS EDGE has to present on their behalf, and they are different
# questions. Without it the edge had one move — forward the caller's own credential to everything —
# and found out at call time whether the door read that. Flows' four tools sat behind a
# deployment-wide operator key this edge does not hold and must never hold, so the answer reached
# the agent as a JSON-RPC result with a 401 inside it: a tool in `tools/list` that cannot work.
#
#   subject   the caller's own credential travels. ALWAYS satisfiable — the caller brought it.
#   admin     a key the DEPLOYMENT holds travels instead, and the caller's does not. Satisfiable
#             only where that key is actually configured, which is why it fails the boot.
#   none      nothing travels.
#
# REQUIRED, not defaulted. A default is what the edge already had — a guess, applied silently to
# every tool, and wrong for four of them. A field that may be omitted reproduces this hole for the
# next manifest, and the next manifest is the one nobody is watching.
AUTHS = {"subject", "admin", "none"}
#: Published literals a stock deploy surface once supplied — the same refusal list flows-api and the
#: services' `config.v1` keep. A key that is in this repository authenticates nobody and everybody.
PLACEHOLDER_KEYS = {"changeme", "change-me", "default", "secret", "vexa-internal-secret",
                    "lite-internal-secret"}


class ManifestError(Exception):
    """A manifest, or a combination of them, that this deployment must not boot with."""


@dataclass(frozen=True)
class Tool:
    name: str
    domain: str
    source: str
    identity: str
    requires: frozenset
    route: Optional[dict]
    base_url_env: Optional[str]
    arguments: tuple = ()
    note: str = ""
    #: which credential this edge presents to the door — see AUTHS.
    auth: str = "subject"
    #: for `auth: admin`, the domain's `{"header": …, "key_env": …}`. None for every other value.
    admin_auth: Optional[dict] = None


@dataclass(frozen=True)
class Entitlement:
    domain: str
    route: dict
    answers: str


@dataclass
class Assembly:
    tools: List[Tool] = field(default_factory=list)
    entitlement: Optional[Entitlement] = None
    absent_domains: Set[str] = field(default_factory=set)
    by_domain: Dict[str, int] = field(default_factory=dict)


def validate(doc: dict) -> dict:
    """One manifest, on its own. Raises :class:`ManifestError` naming what is wrong."""
    if not isinstance(doc, dict) or doc.get("contract") != CONTRACT:
        raise ManifestError(f"not a {CONTRACT} manifest")
    domain = doc.get("domain")
    if not isinstance(domain, str) or not DOMAIN_NAME.match(domain):
        raise ManifestError(
            f"unknown domain {domain!r} — a domain name keys depends_on, requires and "
            f"<DOMAIN>_API_URL, so it must match {DOMAIN_NAME.pattern}")
    if doc.get("source") not in ("oss", "mounted"):
        raise ManifestError(f"{domain}: source must be oss or mounted")

    # A domain's doors are IDENTITY, RUNTIME and ITSELF. Runtime is a primitive — it spawns bots for
    # meetings and workers for agent, has no tools and no person-facing surface — so it is allowed
    # exactly as identity is, and is not a domain that can appear here at all.
    depends = doc.get("depends_on")
    if not isinstance(depends, list) or not set(depends) <= {"identity"}:
        raise ManifestError(
            f"{domain}: depends_on may name identity and nothing else (got {depends!r}) — "
            "identity is the only domain everyone may depend on")
    if domain == "identity" and depends:
        raise ManifestError("identity: depends_on must be empty — it depends on nothing")

    for t in doc.get("tools") or []:
        name = t.get("name")
        if not isinstance(name, str) or not name:
            raise ManifestError(f"{domain}: a tool with no name")
        if t.get("identity") not in IDENTITIES:
            raise ManifestError(f"{domain}/{name}: identity must be one of {sorted(IDENTITIES)}")
        auth = t.get("auth")
        if auth not in AUTHS:
            raise ManifestError(
                f"{domain}/{name}: auth must be one of {sorted(AUTHS)} (got {auth!r}) — every tool "
                "states which credential its door reads. There is no default: the default is what "
                "this edge used to guess, and it guessed wrong for every operator-keyed route.")
        if auth == "admin" and not _admin_auth(doc):
            raise ManifestError(
                f"{domain}/{name}: auth is admin, so {domain} must declare admin_auth "
                '{"header": …, "key_env": …} — a deployment cannot hold a key it cannot spell, '
                "and this edge will not invent the header the door reads.")
        requires = t.get("requires")
        if not isinstance(requires, list) or not requires:
            raise ManifestError(f"{domain}/{name}: requires must name at least one domain")
        bad = sorted(r for r in requires if not (isinstance(r, str) and DOMAIN_NAME.match(r)))
        if bad:
            raise ManifestError(f"{domain}/{name}: requires names {bad} — not domain names")
        # A HARNESS DECLARES ITSELF; IT IS NOT RECOGNISED BY NAME. This exemption used to be
        # `if domain == "rehearse"`, which put a hosted-only domain's name in the OSS assembler and
        # meant a private deployment could not ship a second harness without changing this file.
        # `composes: true` says the same thing in the manifest that needs it — the door across
        # domains is opened by whoever owns the manifest, on the record, once.
        allowed = None if doc.get("composes") is True else {domain, "identity"}
        if allowed is not None and not set(requires) <= allowed:
            raise ManifestError(
                f"{domain}/{name}: requires may name {sorted(allowed)} (got {sorted(requires)}) — "
                "a tool that needs another domain is a composition, and a composition has an owner. "
                'A manifest whose whole job IS composing across domains declares `"composes": true`')
        # PRD 40.8: one authentication path. A tool may not take a credential as an ARGUMENT.
        for arg in t.get("arguments") or []:
            if str(arg).strip().lower() in CREDENTIAL_ARGUMENTS:
                raise ManifestError(
                    f"{domain}/{name}: declares a credential argument {arg!r} — this edge has "
                    "exactly one authentication path (a bearer header, session-bound). A "
                    "credential in an argument list is in the transcript forever.")
        route = t.get("route")
        if route is None:
            # Edge-owned: the assembler serves it itself. Only legal where there is no door.
            if doc.get("base_url_env"):
                raise ManifestError(
                    f"{domain}/{name}: route is null but this domain HAS a door "
                    f"({doc['base_url_env']}) — a tool with a door must name its route")
        else:
            if route.get("method") not in METHODS or not str(route.get("path", "")).startswith("/"):
                raise ManifestError(f"{domain}/{name}: route needs a method and an absolute path")
    ent = doc.get("entitlement")
    if ent is not None:
        if not isinstance(ent, dict) or not ent.get("route") or not ent.get("answers"):
            raise ManifestError(f"{domain}: entitlement needs a route and what it answers")
    return doc


def _admin_auth(doc: dict) -> Optional[dict]:
    """A domain's `{"header", "key_env"}`, or None when it did not declare a usable one."""
    aa = doc.get("admin_auth")
    if not isinstance(aa, dict):
        return None
    header, key_env = str(aa.get("header") or "").strip(), str(aa.get("key_env") or "").strip()
    return {"header": header, "key_env": key_env} if header and key_env else None


def load_mounted(directory: Optional[str]) -> List[dict]:
    """Every manifest a private deployment supplied. EMPTY IS THE OSS PRODUCT, exactly.

    A malformed file FAILS rather than being skipped: skipping it means a paid deployment silently
    missing the tools it paid for, which is the worst outcome available here."""
    if not directory:
        return []
    d = pathlib.Path(directory)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.mcp.tools.v1.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            raise ManifestError(f"mounted manifest {f.name} could not be read: {e}") from e
        if not isinstance(doc, dict):
            raise ManifestError(f"mounted manifest {f.name} is not an object")
        # A file in the mount IS mounted, whatever it claims about itself — otherwise the one
        # property that makes mounts safe (no precedence) could be opted out of by writing a word.
        doc["source"] = "mounted"
        out.append(doc)
    return out


def assemble(manifests: List[dict], *, deployed: Set[str],
             required_domains: Optional[Set[str]] = None,
             env: Optional[dict] = None) -> Assembly:
    """The union, with every combination rule applied. Raises :class:`ManifestError`.

    `env` is the DEPLOYMENT, and it is here for one question: can this edge actually authenticate
    each tool it is about to publish? A tool it cannot is refused now, by name, rather than served
    and refused later by the door — the same fail-direction as every other rule in this module, and
    the one the contract could not express until `auth` existed.
    """
    env = os.environ if env is None else env
    answered = {d.get("domain") for d in manifests}
    for missing in sorted((required_domains or set()) - answered):
        raise ManifestError(
            f"{missing} is deployed but did not answer with a manifest — refusing to boot with a "
            "domain's tools silently absent")

    out = Assembly()
    claimed: Dict[str, tuple] = {}
    for doc in manifests:
        validate(doc)
        domain, source = doc["domain"], doc["source"]
        ent = doc.get("entitlement")
        if ent:
            if out.entitlement is not None:
                raise ManifestError(
                    f"two manifests declare an entitlement hook ({out.entitlement.domain} and "
                    f"{domain}) — 'may this person act' is not a question with two answers")
            out.entitlement = Entitlement(domain=domain, route=ent["route"],
                                          answers=ent["answers"])
        for t in doc.get("tools") or []:
            name = t["name"]
            prior = claimed.get(name)
            if prior:
                raise ManifestError(
                    f"the tool name {name!r} is claimed by two manifests ({prior[0]}/{prior[1]} "
                    f"and {domain}/{source}) — a duplicate is a design question and a person has "
                    "to answer it; a mounted manifest never wins by default")
            claimed[name] = (domain, source)
            if not set(t["requires"]) <= deployed:
                continue
            admin_auth = _admin_auth(doc) if t["auth"] == "admin" else None
            if admin_auth:
                held = str(env.get(admin_auth["key_env"]) or "").strip()
                if not held or held in PLACEHOLDER_KEYS:
                    raise ManifestError(
                        f"{domain}/{name} needs the operator credential this deployment does not "
                        f"hold: {admin_auth['key_env']} is "
                        f"{'a published placeholder' if held else 'unset'}. Refusing to serve a "
                        "tool that would be listed and then refused by its own door — set it, or "
                        "take the tool out of this deployment's manifest.")
            out.tools.append(Tool(
                name=name, domain=domain, source=source, identity=t["identity"],
                requires=frozenset(t["requires"]), route=t.get("route"),
                base_url_env=doc.get("base_url_env"),
                arguments=tuple(t.get("arguments") or ()), note=t.get("note", ""),
                auth=t["auth"], admin_auth=admin_auth))
    for doc in manifests:
        if doc["domain"] not in deployed:
            out.absent_domains.add(doc["domain"])
    for t in out.tools:
        out.by_domain[t.domain] = out.by_domain.get(t.domain, 0) + 1
    return out
