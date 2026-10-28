"""routers/held.py — what the privacy hold kept back, for the person it belongs to (D6, H7).

    GET    /api/held          the person's held items: surface, where, when, which private sources
    GET    /api/held/{code}   one item with its content — the share card's body
    DELETE /api/held/{code}   the person discards it

The person's OWN session only: a worker (any delegated caller) is refused, so no agent reads back
what was withheld from it, and nobody reads another person's items (the key is the caller's subject,
and the ciphertext is bound to it). Releasing a held write is the person saying ``confirm <code>`` in
their chat (`private_taint`), not a route here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from control_plane import ceiling, held_store


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]

    def _person(request: Request) -> str:
        if ceiling.is_delegated(request):
            raise HTTPException(status_code=403, detail={
                "refused": "person_only", "note": "held writes are opened by the person, never by an agent"})
        return str(subject_of(request))

    def _store():
        store = held_store.current()
        if store is None:
            raise HTTPException(status_code=503, detail={
                "refused": "held_store_unavailable", "fault": {"code": "held_store_unavailable"},
                "note": "held writes cannot be read on this deployment right now"})
        return store

    @router.get("/api/held")
    def held_list(request: Request):
        who = _person(request)
        return {"items": [held_store.summary(x) for x in _store().list(who)]}

    @router.get("/api/held/{code}")
    def held_get(code: str, request: Request):
        who = _person(request)
        item = _store().get(who, code)
        if item is None:
            raise HTTPException(status_code=404, detail="no such held item")
        return {**held_store.summary(item), "payload": item.get("payload") or {}}

    @router.delete("/api/held/{code}")
    def held_discard(code: str, request: Request):
        who = _person(request)
        if not _store().discard(who, code):
            raise HTTPException(status_code=404, detail="no such held item")
        return {"discarded": code}

    return router
