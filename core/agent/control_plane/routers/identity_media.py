"""routers/identity_media.py — a person's photo, for the people who know them (#1783, UI 15g).

    GET /api/people/{subject}/photo   the photo identity keeps for ``subject``, when the caller is that
                                      person or shares a workspace or a chat with them; else 404

The rules are ``control_plane/identity_media.py``. The browser reaches this through the gateway with
the person's own credential; the subject of the CALLER is the gateway's ``X-User-Id`` (P20), never a
parameter. ``Cache-Control: private`` — a shared cache never holds a face.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response

from control_plane import identity_media, people_directory


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    store = d.get("session_store")
    wsr, mindex = d.get("wsr"), d.get("mindex")
    colleagues_of = d.get("people_colleagues")      # tests: a fake directory
    if d.get("settings") is not None:
        identity_media.configure(d["settings"])

    def _colleagues(viewer: str) -> list:
        if colleagues_of is not None:
            return list(colleagues_of(viewer))
        if wsr is None:
            return []
        return people_directory.mentionable(
            wsr.root, viewer, lambda s: list(mindex.list(s) or []) if mindex is not None else [])

    def _sessions(who: str) -> list:
        return list(store.list_for(who) or []) if store is not None else []

    @router.get("/api/people/{subject}/photo")
    def person_photo(subject: str, request: Request, v: str = Query(default="", max_length=64)):
        """A person's photo — theirs to choose (an upload, else their sign-in provider's picture, else
        their Gravatar). Only for the person and people who share a workspace or a chat with them; 404
        otherwise and when there is none (draw initials). Never a face looked up on the web."""
        viewer = str(subject_of(request))
        if not identity_media.SUBJECT.match(subject or ""):
            raise HTTPException(status_code=404, detail="no photo")
        if not identity_media.may_see(viewer, subject, colleagues=_colleagues, sessions_of=_sessions):
            raise HTTPException(status_code=404, detail="no photo")
        got = identity_media.photo(subject, v)
        if not got:
            raise HTTPException(status_code=404, detail="no photo")
        body, ctype, etag = got
        headers = {"Cache-Control": "private, max-age=30", "X-Content-Type-Options": "nosniff",
                   "Content-Security-Policy": "default-src 'none'; sandbox"}
        if etag:
            headers["ETag"] = etag
        return Response(content=body, media_type=ctype, headers=headers)

    return router
