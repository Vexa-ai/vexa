"""regime.py — the verbs that need a person in the loop, refused to a worker dispatched without one.

A worker acts for a person with a delegation token; the gateway carries the dispatch's ceiling on
the signed identity (gateway-identity.v1 `delegation`), which the identity door here rebuilds as
`x-user-regime` · `x-user-delegation-workspaces` · `x-user-delegation-target`. `human` means the
person is in the chat this turn. Anything else — a routine, an event, a meeting-triggered run, or a
regime this service does not know — runs unwatched.

The verbs below are the ones whose effect reaches beyond the person's own reading: they put a bot
into a meeting other people are in, hand a transcript to someone else, bind a meeting to a shared
workspace, or destroy a meeting or a recording. They run for a person's own credential, for the
internal tier, and for a worker in the human regime; a delegated identity in any other regime is
refused with the same answer agent-api gives (`require_person` there), so a worker reads one
refusal everywhere.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from . import identity_token
# The rule and the refusal are gateway-identity.v1's (the vendored identity_token), shared with
# agent-api byte for byte.
from .identity_token import REFUSAL


def require_person(request: Request) -> None:
    """FastAPI dependency: 403 for a delegated identity whose regime is not ``human``
    (`identity_token.is_unwatched`: an empty or unknown regime included)."""
    if identity_token.is_unwatched(request.headers):
        raise HTTPException(status_code=403, detail=REFUSAL)
