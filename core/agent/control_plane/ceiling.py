"""ceiling.py — where a caller may act in agent-api, and whether a person is in the loop.

A worker acts for a person with a delegation token. The gateway signs the dispatch's delegation into
the identity (gateway-identity.v1 `delegation`), and the identity door rebuilds it as the delegation
headers: the regime (`human` when the person is in the chat this turn), the isolation set of
workspaces the dispatch was granted, and the chat's target workspace. A person's own credential and
the internal tier carry none of them.

This module is the one place agent-api reads those headers, by the names the vendored
`identity_token.DELEGATION_HEADERS` gives them:

- `is_delegated` / `is_unwatched` — the person-in-the-loop rule. Delegated means any delegation
  header is on the identity, an empty one included; unwatched means delegated and a regime other
  than `human` (an unknown or empty regime included). The fail direction is closed both times;
- `require_person` — the refusal (`REFUSAL`, the body meeting-api's `regime.py` answers with, so a
  worker reads one refusal everywhere) a verb that needs a person gives an unwatched caller, and
  `refuse_delegated` for a verb no delegated caller may use at all;
- `delegation_allows` / `require_in_ceiling` — the workspace ceiling, applied by every resolver of a
  named workspace before it resolves anything;
- `reads_within` — the same ceiling on a read that walks a person-wide set (their memberships, their
  mounts, the workspaces a link names) instead of one named workspace: the set is narrowed to what
  the resolvers would let this caller read;
- `write_slug` — where a page verb acts when the caller names no workspace.

Which verbs need a person is not decided here: `route_policy.py` reads it from `routes.v1.json`
and applies `require_person` for the matched route.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import HTTPException, Request

from control_plane import identity_token
# `REFUSAL` is what a verb that needs a person answers a worker dispatched without one: the vendored
# gateway-identity.v1 body, the same one meeting-api's `regime.py` answers with.
from control_plane.identity_token import CLAIM_HEADERS, DELEGATION_HEADERS, REFUSAL

logger = logging.getLogger("agent_api.ceiling")

WORKSPACES_HEADER = DELEGATION_HEADERS["workspaces"]
TARGET_HEADER = DELEGATION_HEADERS["target"]
SUBJECT_HEADER = CLAIM_HEADERS["sub"]


def refusal(reason: str, instruction: str) -> dict:
    """The body of a refusal a worker reads: what was refused, and what to do instead."""
    return {"status": "refused", "reason": reason, "instruction": instruction}




def refused(request: "Request", status: int, detail, *, reason: str, **fields) -> HTTPException:
    """The HTTPException for a refusal, logged once as one structured line naming who asked, where
    and why. Never logs a header value other than the subject."""
    logger.warning(json.dumps({
        "event": "refused", "reason": reason, "status": status,
        "method": getattr(request, "method", ""), "path": getattr(getattr(request, "url", None), "path", ""),
        "subject": (request.headers.get(SUBJECT_HEADER) or "").strip(), **fields}))
    return HTTPException(status_code=status, detail=detail)


#: WHERE A PAGE VERB WRITES, said once for every one of them. An omitted `slug` is where the
#: conversation is WORKING: a worker's identity carries the chat's target (`x-user-delegation-target`),
#: and a caller with none — a person's own client, the terminal — writes on their own desk as before.
#: `personal` (or `desk`) names the desk explicitly, which is how *"note this on my desk"* still works
#: from a chat working somewhere else.
WRITE_SLUG_HELP = ("the workspace to act in. Omit it to act where this conversation is working; "
                   "pass `personal` for the person's own desk, or a workspace's slug for one act "
                   "anywhere else they may write.")
#: The words that name the person's own desk on a page verb.
DESK_ALIASES = frozenset({"personal", "desk"})


def write_slug(request: "Request", asked: Optional[str]) -> Optional[str]:
    """The workspace a page verb acts in: the caller's explicit `slug`, else the chat's target, else
    the desk (``None``). The target is a DEFAULT, never a grant: the resolved slug is held to a
    delegated dispatch's ceiling here, and every route still authorizes it exactly as it would one
    the caller had typed."""
    named = (asked or "").strip()
    if named in DESK_ALIASES:
        return None
    resolved = named or (request.headers.get(TARGET_HEADER) or "").strip() or None
    require_in_ceiling(request, resolved)
    return resolved


def delegation_allows(request: "Request", slug: Optional[str]) -> bool:
    """May this caller address workspace ``slug``? A worker dispatched without a person carries the
    dispatch's isolation set (``x-user-delegation-workspaces``); ``*`` — and every caller that is not
    a delegated worker — is bounded by the account alone. An EMPTY slug, or the caller's own id, is
    the caller's own workspace and always in scope: the uid decides it, not the caller."""
    target = (slug or "").strip()
    ceiling = request.headers.get(WORKSPACES_HEADER)
    if ceiling is None or ceiling.strip() == "*" or not target:
        return True
    if target == (request.headers.get(SUBJECT_HEADER) or "").strip():
        return True
    return target in {w.strip() for w in ceiling.split(",") if w.strip()}


#: The workspaces every subject reads, so a read is never held to a ceiling over them: the company
#: layer, mounted read-only into every worker (`_read_target` holds only a WRITE there to it).
READ_BY_EVERYONE = frozenset({"_global"})


def reads_within(request: "Request", slug: Optional[str]) -> bool:
    """May this caller READ workspace ``slug`` under its dispatch's ceiling? The rule the
    named-workspace resolvers apply on a read, for the routes that walk a person-wide set — the
    person's memberships, their mounts, the workspaces a page's links name — rather than resolve
    one named workspace. A caller with no ceiling reads everything its account does, as before."""
    return (slug or "").strip() in READ_BY_EVERYONE or delegation_allows(request, slug)


def is_delegated(request: "Request") -> bool:
    """Does the caller act under a delegation token? `identity_token.is_delegated`: any delegation
    header on the identity, an empty one included."""
    return identity_token.is_delegated(request.headers)


def is_unwatched(request: "Request") -> bool:
    """Is the caller a worker dispatched without a person in the loop? `identity_token.is_unwatched`:
    delegated, and a regime other than `human` (unknown, missing or empty included)."""
    return identity_token.is_unwatched(request.headers)


def require_in_ceiling(request: "Request", *slugs: Optional[str]) -> None:
    """Refuse (403) any named workspace outside a delegated dispatch's ceiling — the check every
    resolver of a named workspace runs before it resolves anything, and every route that names one
    without a resolver runs before it acts."""
    for slug in slugs:
        if not delegation_allows(request, slug):
            raise refused(request, 403, {
                "refused": "out_of_scope", "workspace": str(slug),
                "why": "this session was dispatched with access to a named set of workspaces and "
                       "that is not one of them"}, reason="out_of_scope", workspace=str(slug))


def require_person(request: Request) -> None:
    """Refuse (403 `REFUSAL`) a verb that needs a person in the loop when the caller runs without
    one. `human` is the only regime these verbs run under; the fail direction on a verb that reads a
    mailbox, spends a credential or loads a repository is closed."""
    if is_unwatched(request):
        raise refused(request, 403, REFUSAL, reason=REFUSAL["reason"])


def refuse_delegated(request: Request, *, reason: str, instruction: str) -> None:
    """Refuse (403) a verb no delegated caller may use, whatever its regime — one a person starts
    themselves. The route names its own reason and instruction."""
    if is_delegated(request):
        raise refused(request, 403, refusal(reason, instruction), reason=reason)
