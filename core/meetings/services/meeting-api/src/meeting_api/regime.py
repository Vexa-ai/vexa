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

A ROUTINE ITS PERSON GRANTED A VERB passes for that verb alone (flows phase 0). A person who makes a
routine in their chat to do one such thing — "send a bot to this meeting at 12:13" — grants it the
verb by name when they create it; the routine's own unwatched turn carries the grant, signed
(gateway-identity.v1 `delegation.grants` beside `delegation.flow`), and `require_person_or_granted`
lets exactly that verb through and logs each use. Every other verb still refuses it.

A worker whose turn other people read (``delegation.shared``, ADR-0048) is refused EVERY route here
(`refuse_shared`): a shared conversation's answer may use only what every participant can read, and
a person's meetings are theirs alone.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from starlette.exceptions import WebSocketException
from starlette.requests import HTTPConnection

from . import identity_token
# The rule and the refusal are gateway-identity.v1's (the vendored identity_token), shared with
# agent-api byte for byte.
from .identity_token import REFUSAL, SHARED_REFUSAL

_log = logging.getLogger("meeting_api.regime")


def require_person(request: Request) -> None:
    """FastAPI dependency: 403 for a delegated identity whose regime is not ``human``
    (`identity_token.is_unwatched`: an empty or unknown regime included)."""
    if identity_token.is_unwatched(request.headers):
        raise HTTPException(status_code=403, detail=REFUSAL)


def require_person_or_granted(action: str):
    """A FastAPI dependency like :func:`require_person`, except that a routine whose person granted
    it ``action`` (delegation.v1 ``UNATTENDED_GRANTS``) passes — that routine, that verb, nothing
    else. Each granted use is logged with the routine it was for, so the grant is audited."""
    def guard(request: Request) -> None:
        if identity_token.refuses(request.headers, action):
            raise HTTPException(status_code=403, detail=REFUSAL)
        if identity_token.is_unwatched(request.headers):
            _log.info("routine_grant_used", extra={
                "action": action,
                "flow": request.headers.get(identity_token.DELEGATION_HEADERS["flow"], ""),
                "user_id": request.headers.get("x-user-id", "")})
    return guard


def refuse_shared(request: HTTPConnection) -> None:
    """App-level dependency: 403 for a delegated identity whose turn other people read
    (`identity_token.is_shared`, gateway-identity.v1 ``delegation.shared``, ADR-0048 decision 4).
    Everything this service holds — meetings, transcripts, recordings, bots, the person's calendar
    sync — is its person's, so none of it may reach an answer every participant reads. Installed on
    the app, so every route is covered and none asks for it by hand — a socket too (it takes the
    connection, not a Request, and refuses a socket by closing it with policy violation)."""
    # A WORKSPACE principal (D2: a room's turn) is shared by construction, so a missing
    # `shared` header never reads as a private turn here.
    if identity_token.is_shared(request.headers) or identity_token.is_workspace_principal(request.headers):
        # ONE EXCEPTION (#1783, collaboration rehearsal 2026-10-14): sending a bot from a chat its
        # members share, into a meeting bound to a workspace they all read. That meeting is the
        # workspace's, not the person's, so the leak rule has nothing to keep from the others. The
        # route checks the workspace against the shared turn's ceiling (`shared_bot_workspace`).
        if (request.scope.get("type") == "http" and request.scope.get("method") == "POST"
                and request.scope.get("path", "").rstrip("/") == "/bots"):
            request.state.shared_turn = True
            return
        # …AND READING ONE MEETING'S TRANSCRIPT, which the route then allows only for a meeting bound
        # to a workspace inside the turn's ceiling (`shared_meeting_readable`). Search and lists stay
        # refused: they range over the person's own meetings.
        path = request.scope.get("path", "")
        if (request.scope.get("type") == "http" and request.scope.get("method") == "GET"
                and path.startswith("/transcripts/") and not path.startswith("/transcripts/search")):
            request.state.shared_turn = True
            return
        if request.scope.get("type") == "websocket":
            raise WebSocketException(code=1008, reason=SHARED_REFUSAL["reason"])
        raise HTTPException(status_code=403, detail=SHARED_REFUSAL)


def shared_bot_workspace(request: Request, workspace_id) -> None:
    """For a shared turn's bot (`refuse_shared` let it through): 403 with the shared refusal unless
    the meeting is bound to a workspace inside the turn's ceiling — the workspaces every person in
    the chat reads. A private bot (no workspace) or one in a workspace outside it is the person's
    own and stays refused."""
    if not getattr(request.state, "shared_turn", False):
        return
    raw = (request.headers.get(identity_token.DELEGATION_HEADERS["workspaces"]) or "").strip()
    ceiling = {w.strip() for w in raw.split(",") if w.strip()}
    if not workspace_id or raw == "*" or workspace_id not in ceiling:
        raise HTTPException(status_code=403, detail=SHARED_REFUSAL)


def shared_meeting_readable(request: Request, doc) -> None:
    """For a shared turn's transcript read (`refuse_shared` let it through): 403 with the shared
    refusal unless the meeting is bound to a workspace inside the turn's ceiling — then every person
    in the chat already reads it as a member. A private meeting stays the person's own."""
    if not getattr(request.state, "shared_turn", False):
        return
    data = (doc or {}).get("data") if isinstance(doc, dict) else None
    ws = (data or {}).get("workspace_id") if isinstance(data, dict) else None
    raw = (request.headers.get(identity_token.DELEGATION_HEADERS["workspaces"]) or "").strip()
    ceiling = {w.strip() for w in raw.split(",") if w.strip()}
    if not ws or raw == "*" or ws not in ceiling:
        raise HTTPException(status_code=403, detail=SHARED_REFUSAL)
