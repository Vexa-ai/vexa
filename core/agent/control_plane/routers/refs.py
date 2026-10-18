"""refs.py — universal references over HTTP (#1783): resolve one, and ask an owner for access.

``GET /api/refs/resolve?ref=`` (MCP ``ref_resolve``) answers what a reference points at for the
person asking — the rules are `control_plane/universal_refs.py`. ``POST /api/refs/request-access``
tells the owner of a workspace, chat or page that this person asks for access (notification kind
``access_requested``); the asker learns only that the request was sent.

Every lookup here goes through the access decision its object already has: the workspace registry's
``access_for`` (narrowed by a delegated dispatch's ceiling), the session store's participants (and,
in a shared turn, every participant of the conversation it answers into — the leak rule), the one
meeting access decision, and the join-link store.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import identity_token, join_links
from control_plane import link_resolver as link_resolver_mod
from control_plane import universal_refs as refs_mod
from control_plane import workspace_ids as ids_mod
from control_plane.ceiling import reads_within
from control_plane.peer_lookups import meeting_access_check
from control_plane.routers.chat_read import _unit_session
from control_plane import session_sync


class RefAccessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1, max_length=2000,
                     description="the reference: a /r/<kind>/<id> link or a [[kind:id]] chip")


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    wsr = d["wsr"]
    registry = d["workspace_registry"]
    is_member = d["_ws_is_member"]
    store = d.get("session_store")
    notifier = d.get("notifier")
    meeting_access = meeting_access_check(d["_meeting_owner_lookup"], wsr.root)

    def _shared_readers(request: Request, subject: str) -> Optional[set]:
        if store is None or not identity_token.is_shared(request.headers):
            return None
        here = _unit_session(identity_token.session_of(request.headers), subject)
        row = session_sync.resolve(store, subject, here, create=False) if here else None
        if row is None:
            return {subject, "\x00nobody-else-known"}
        return {p["principal"] for p in store.participants(row["id"]) if p.get("kind") == "human"}

    def _seams(request: Request, subject: str) -> refs_mod.Seams:
        within = lambda ws: reads_within(request, ws)  # noqa: E731

        def workspace(wid):
            rec = registry.get(wid)
            access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=is_member)
            if rec and access == ids_mod.ACCESS_READABLE and not within(str(rec.get("slug") or "")):
                access = ids_mod.ACCESS_NOT_YOURS
            return rec, access

        def page(wid, target):
            return link_resolver_mod.resolve(f"ws:{wid}/{target}", subject=subject, root=wsr.root,
                                             registry=registry, is_member=is_member, within=within)

        def chat(cid):
            if store is None:
                return None, False
            row = store.get(cid)
            if row is None:
                return None, False
            ok = store.can_read(cid, subject)
            if ok and identity_token.is_unwatched(request.headers) and row.get("owner") != subject:
                ok = False
            readers = _shared_readers(request, subject)
            if ok and readers is not None and not all(store.can_read(cid, p) for p in readers):
                ok = False
            return row, ok

        def meeting(mid):
            try:
                row = meeting_access(subject, mid, within=within)
                if row is None or not identity_token.is_shared(request.headers):
                    return row
                # A TURN OTHER PEOPLE READ (the leak rule, as the chat lookup applies it): the meeting
                # is readable only when every person in the conversation may read it.
                readers = _shared_readers(request, subject)
                if not readers or any(p.startswith("\x00") for p in readers):
                    return None
                if all(p == subject or meeting_access(p, mid, within=within) is not None for p in readers):
                    return row
                return None
            except Exception:  # noqa: BLE001 — an unreachable meeting service reads as "cannot tell"
                return None

        def join(token):
            found = join_links.find(wsr.root, token)
            if found is None:
                return None
            kind, target_id, rec = found
            if join_links.why_not(rec) is not None:
                return None
            if kind == "workspace":
                ws = registry.by_slug(target_id) or {}
                return {"target_kind": kind, "target_id": ws.get("id") or target_id,
                        "name": str(ws.get("name") or target_id)}
            target = join_links.chat_target()
            try:
                name = str(target.title(target_id)) if target else ""
            except Exception:  # noqa: BLE001
                name = ""
            return {"target_kind": kind, "target_id": target_id, "name": name or "untitled"}

        return refs_mod.Seams(workspace=workspace, page=page, chat=chat, meeting=meeting, join=join)

    def _resolve(request: Request, ref: str) -> dict:
        subject = subject_of(request)
        try:
            return refs_mod.resolve(ref, _seams(request, subject))
        except refs_mod.RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @router.get("/api/refs/resolve")
    def ref_resolve(request: Request, ref: str = Query(..., min_length=1, max_length=2000)):
        """What a reference points at, for you: a link `https://<host>/r/<kind>/<id>`, a chip
        `[[kind:id]]` or a join link. Kinds: workspace, chat, page, entity, meeting, flow, sensor.
        `access` is readable (with `label` and where it `open`s), not-yours (nothing about it is
        shown; `request_access` says an owner can be asked — offer it, never guess the content),
        unavailable, gone, or grant (a join link: what it lets the holder into).
        Write a reference into a reply as `[[kind:id|label]]` and in mail or a calendar body as its URL."""
        return _resolve(request, ref)

    @router.post("/api/refs/request-access")
    def ref_request_access(request: Request, body: RefAccessBody = Body(...)):
        """Ask the owner of a workspace, chat or page you cannot open for access. They get a
        notification naming you and the object; you are told only that it was sent."""
        ref = body.ref
        answer = _resolve(request, ref)
        if answer["access"] == refs_mod.GONE:
            raise HTTPException(status_code=404, detail="nothing by that reference")
        if answer["access"] != refs_mod.NOT_YOURS or not answer["request_access"]:
            raise HTTPException(status_code=409, detail="there is nobody to ask for this one")
        subject = subject_of(request)
        owner, kind, oid, name = _owner_of(answer)
        if not owner or notifier is None:
            raise HTTPException(status_code=409, detail="there is nobody to ask for this one")
        notifier.emit([owner], kind="access_requested", actor=subject,
                      actor_name=request.headers.get("x-user-email") or subject,
                      object_kind=kind, object_id=oid, object_name=name, url=answer["url"])
        return {"requested": True}

    def _owner_of(answer: dict) -> tuple:
        """(owner, object kind, object id, the name the OWNER sees) — read only to address the owner."""
        k, i = answer["kind"], answer["id"]
        if k == "chat":
            row = store.get(i) if store is not None else None
            return (row or {}).get("owner"), "chat", i, (row or {}).get("title") or ""
        wid = i.split("/", 1)[0]
        rec = registry.get(wid) or {}
        if k == "workspace":
            return rec.get("owner"), "workspace", i, rec.get("name") or ""
        return rec.get("owner"), "page", i, Path(i.split("/", 1)[-1]).stem

    return router
