"""ceiling.py — where a caller may act in agent-api, and whether a person is in the loop.

A worker acts for a person with a delegation token. The gateway signs the dispatch's delegation into
the identity (gateway-identity.v1 `delegation`), and the identity door rebuilds it as the delegation
headers: the regime (`human` when the person is in the chat this turn), the isolation set of
workspaces the dispatch was granted, and the chat's target workspace. A person's own credential and
the internal tier carry none of them.

This module is the one place agent-api reads those headers:

- `require_person` — the refusal a verb that needs a person in the loop gives a worker dispatched
  without one;
- `delegation_allows` / `require_in_ceiling` — the workspace ceiling, applied by every resolver of a
  named workspace before it resolves anything;
- `write_slug` — where a page verb acts when the caller names no workspace.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request


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
    the desk (``None``). The target is a DEFAULT, never a grant — every route still authorizes the
    resolved slug exactly as it would one the caller had typed."""
    named = (asked or "").strip()
    if named in DESK_ALIASES:
        return None
    if named:
        return named
    return (request.headers.get("x-user-delegation-target") or "").strip() or None


def delegation_allows(request: "Request", slug: Optional[str]) -> bool:
    """May this caller address workspace ``slug``? A worker dispatched without a person carries the
    dispatch's isolation set (``x-user-delegation-workspaces``); ``*`` — and every caller that is not
    a delegated worker — is bounded by the account alone. An EMPTY slug, or the caller's own id, is
    the caller's own workspace and always in scope: the uid decides it, not the caller."""
    target = (slug or "").strip()
    ceiling = request.headers.get("x-user-delegation-workspaces")
    if ceiling is None or ceiling.strip() == "*" or not target:
        return True
    if target == (request.headers.get("x-user-id") or "").strip():
        return True
    return target in {w.strip() for w in ceiling.split(",") if w.strip()}


def unwatched_worker(request: "Request") -> bool:
    """Is the caller a worker dispatched without a person in the loop? A delegated identity
    (any `x-user-delegation-*`/`x-user-regime` on the signed identity) whose regime is not `human`.
    A person's own credential and the internal tier carry none of these."""
    regime = (request.headers.get("x-user-regime") or "").strip().lower()
    delegated = bool(regime) or any(h in request.headers for h in (
        "x-user-delegation-workspaces", "x-user-delegation-target"))
    return delegated and regime != "human"


def require_in_ceiling(request: "Request", *slugs: Optional[str]) -> None:
    """Refuse (403) any named workspace outside a delegated dispatch's ceiling — the one check every
    route that names a workspace in its path or body runs before it acts, reads aside."""
    for slug in slugs:
        if not delegation_allows(request, slug):
            raise HTTPException(status_code=403, detail={
                "refused": "out_of_scope", "workspace": str(slug),
                "why": "this session was dispatched with access to a named set of workspaces and "
                       "that is not one of them"})


def require_person(request: Request) -> None:
    """Refuse a verb that needs a person in the loop when the caller is a worker dispatched
    without one. The gateway carries a delegation token's regime as ``x-user-regime`` (signed);
    a person's own credential carries none. ``human`` is the only regime these verbs run under,
    and an unknown regime is refused like ``autonomous`` — the fail direction on a verb that
    reads a mailbox, spends a credential or loads a repository is closed."""
    regime = (request.headers.get("x-user-regime") or "").strip().lower()
    # A delegated identity that names no regime is not a human one either: the ceiling headers
    # mark a worker whether or not identity stated why it was dispatched.
    delegated = bool(regime) or any(h in request.headers for h in (
        "x-user-delegation-workspaces", "x-user-delegation-target"))
    if delegated and regime != "human":
        raise HTTPException(status_code=403, detail={
            "status": "refused", "reason": "human_session_required",
            "instruction": "This session runs without a person in the loop. Record what you "
                           "wanted to do and stop; do not retry it another way."})
