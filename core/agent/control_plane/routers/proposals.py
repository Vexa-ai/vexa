"""routers/proposals.py — the desk's short list, over HTTP (Vexa-ai/vexa#1614).

Three doors on one store (`shared/proposals.py`):

    GET  /api/proposals              what the empty chat renders — OPEN rows, newest first, <=10
    POST /api/proposals              one agent proposing one job it saw
    POST /api/proposals/resolve      the row leaves — `ran` when its act fired, `dismissed` when not

THE READ IS THE POINT OF THE ROUTE. Founder ruling on the chips (#1584, restated on #1614): the row
is *rendered by the client from state, no model call to show it*. So this is a file read behind an
identity check and nothing else — no dispatch, no turn, no composition. A surface that had to ask a
model what to offer would cost a turn every time somebody opened an empty chat, and would answer
differently each time it was asked.

THE SUBJECT IS THE CALLER'S, ALWAYS. `subject_of` is the gateway-injected identity (P20); no body or
query names a desk. Flows writes onto somebody else's desk by calling this route AS THEM — the same
`X-User-Id` shape `ag.workspace_write` already uses for the meeting drop — so there is exactly one
answer to "whose list is this", on every door.
"""
from __future__ import annotations

from control_plane.api_shared import logger
from fastapi import APIRouter, Body, HTTPException, Request
from shared import proposals as store


def build(**d) -> APIRouter:
    """The proposals routes, bound to one app's dependencies."""
    router = APIRouter()
    subject_of = d['subject_of']
    wsr = d['wsr']

    def _desk(request: Request):
        try:
            return wsr.workspace_dir(subject_of(request))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject") from None

    @router.get("/api/proposals")
    def list_proposals(request: Request):
        """The short list this person's empty chat offers: OPEN rows, newest first, at most ten.

        A desk that does not exist yet has no list, and that is a 200 with nothing in it — the chat
        still opens, and the standing acts the client owns are still there."""
        return {"items": store.open_items(_desk(request)), "max": store.OPEN_MAX}

    @router.post("/api/proposals", status_code=201)
    def propose(request: Request, body: dict = Body(...)):
        """One agent, one job it saw. `source` + `act` are required and are together the identity:
        proposing the same job twice updates the row rather than adding a second."""
        desk = _desk(request)
        try:
            row = store.add(desk,
                            source=str(body.get("source") or ""),
                            act=str(body.get("act") or ""),
                            source_label=str(body.get("source_label") or ""),
                            by=str(body.get("by") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        logger.info("proposal %s by=%s source=%s added=%s",
                    row["id"], row.get("by") or "-", row["source"], row["added"])
        return row

    @router.post("/api/proposals/resolve")
    def resolve_proposal(request: Request, body: dict = Body(...)):
        """The row leaves. `ran` is a click that fired the act; `dismissed` is the person saying no,
        and the two are kept apart because only one of them is feedback about the proposal."""
        iid = str(body.get("id") or "").strip()
        status = str(body.get("status") or "").strip()
        if not iid:
            raise HTTPException(status_code=400, detail="which proposal? — `id` is required")
        try:
            row = store.resolve(_desk(request), iid, status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if row is None:
            raise HTTPException(status_code=404, detail="no such proposal on this desk")
        return row

    return router
