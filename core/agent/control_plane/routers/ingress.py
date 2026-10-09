"""routers/ingress.py — The two internal doors a dispatch arrives through: `/invocations`, where the
internal tier or a routine job agent-api signed hands over a unit.v1 dispatch, and `/events`, where
the internal tier hands over an event.v1 event that becomes one. Both bodies name the person the
turn runs as, so the CALLER is authenticated before either is read (`dispatch_sink.py`).

Moved out of `routers/chats.py` byte for byte; `build()` rebinds each dependency to the name the
handlers already used.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request
from jsonschema.exceptions import ValidationError

from control_plane import dispatch_sink
from control_plane.events import event_to_invocation
from shared import delegation as delegation_mod


def build(**d) -> APIRouter:
    """The ingress routes, bound to one app's dependencies."""
    router = APIRouter()
    dispatcher = d['dispatcher']
    settings = d['settings']

    def _internal_secret() -> str:
        return settings.internal_api_secret.get_secret_value() if settings is not None else ""

    def _sink_caller(request: Request, body) -> str:
        """Who handed this dispatch over: ``"internal"``, ``"signed"`` (a routine job agent-api
        composed itself), or ``""`` — refused. See `dispatch_sink.py`."""
        secret = _internal_secret()
        if dispatch_sink.internal_caller(secret, request.headers.get("x-internal-secret", "")):
            return "internal"
        if dispatch_sink.verify(secret, body, request.headers.get(dispatch_sink.HEADER, "")):
            return "signed"
        return ""

    @router.post("/invocations", status_code=202)
    def invocations(request: Request, invocation: dict = Body(...)):
        """The dispatcher sink — the internal tier, or a routine job agent-api signed when it compiled
        it (`dispatch_sink.py`), POSTs a unit.v1 dispatch here. The body names the person the turn
        runs as, so the CALLER is authenticated before the body is read; nobody else is heard."""
        caller = _sink_caller(request, invocation)
        if not caller:
            raise HTTPException(status_code=401, detail="the dispatch sink takes the internal tier "
                                                        "or a dispatch agent-api signed")
        # A signed job is a routine: it never asks for a person in the loop, whatever it says.
        if caller != "internal" and str(invocation.get("trigger") or "") in delegation_mod.HUMAN_TRIGGERS:
            raise HTTPException(status_code=403, detail="a signed dispatch runs without a person")
        try:
            workload_id = dispatcher.dispatch(invocation)
        except ValidationError as e:  # non-conformant unit.v1 envelope — fail loud (P18)
            raise HTTPException(status_code=400, detail=f"invalid unit.v1 dispatch: {e.message}")
        return {"workload_id": workload_id}
    @router.post("/events", status_code=202)
    def events(request: Request, event: dict = Body(...)):
        # The event names the person it is about; only the internal tier may say who that is.
        if _sink_caller(request, None) != "internal":
            raise HTTPException(status_code=401, detail="the event sink takes the internal tier")
        try:
            invocation = event_to_invocation(event)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=f"invalid event.v1: {e.message}")
        except ValueError as e:  # no plan carried — fail loud (P18)
            raise HTTPException(status_code=422, detail=str(e))
        workload_id = dispatcher.dispatch(invocation)
        return {"workload_id": workload_id, "trigger": invocation["trigger"]}

    return router
