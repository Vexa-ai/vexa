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


class AudienceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1, max_length=2000)
    room: Optional[str] = Field(default=None, max_length=128, description="a room's session id")
    person: Optional[str] = Field(default=None, max_length=200, description="a person's subject or email")


class RefAccessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1, max_length=2000,
                     description="the reference: a /r/<kind>/<id> link or a [[kind:id]] chip")


def seams_for(request: Request, subject: str, d: dict, *, cards: Optional[list] = None,
              page_workspace: Optional[str] = None) -> refs_mod.Seams:
    """THE resolver's seams for one viewer (PRD 0.13.4 D18): each object's own access decision.
    Shared with the page renderer (`routers/desktop.py` POST /api/desktop/page), so a chip in a
    chat, a pasted URL and a reference in a README are answered by the same code.

    ``cards`` are the crm cards the terminal read with the viewer's own key (agent-api cannot read
    crm); a card not among them answers ``unavailable``. ``page_workspace`` is the page's workspace,
    for a ``file:`` reference relative to it."""
    wsr = d["wsr"]
    registry = d["workspace_registry"]
    is_member = d["_ws_is_member"]
    store = d.get("session_store")
    meeting_access = meeting_access_check(d["_meeting_owner_lookup"], wsr.root)
    within = lambda ws: reads_within(request, ws)  # noqa: E731

    def _shared_readers() -> Optional[set]:
        if store is None or not identity_token.is_shared(request.headers):
            return None
        here = _unit_session(identity_token.session_of(request.headers), subject)
        row = session_sync.resolve(store, subject, here, create=False) if here else None
        if row is None:
            return {subject, "\x00nobody-else-known"}
        return {p["principal"] for p in store.participants(row["id"]) if p.get("kind") == "human"}

    def workspace(wid):
        rec = registry.get(wid) or registry.by_slug(wid)
        access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=is_member)
        if rec and access == ids_mod.ACCESS_READABLE and not within(str(rec.get("slug") or "")):
            access = ids_mod.ACCESS_NOT_YOURS
        return rec, access

    def page(wid, target):
        rec = registry.get(wid) or registry.by_slug(wid)
        if rec and rec.get("id"):
            wid = rec["id"]
        return link_resolver_mod.resolve(f"ws:{wid}/{target}", subject=subject, root=wsr.root,
                                         registry=registry, is_member=is_member, within=within)

    def chat(cid):
        if store is None:
            return None, False
        row = store.get(cid)
        if row is None:
            # the person's own conversation by its name (the terminal opens own chats by name)
            try:
                row = store.by_name(subject, cid)
            except Exception:  # noqa: BLE001
                row = None
            if row is None:
                return None, False
            cid = row["id"]
        ok = store.can_read(cid, subject)
        if ok and identity_token.is_unwatched(request.headers) and row.get("owner") != subject:
            ok = False
        readers = _shared_readers()
        if ok and readers is not None and not all(store.can_read(cid, p) for p in readers):
            ok = False
        return row, ok

    def message(cid, mid):
        """A message is readable exactly when its conversation is; it adds only its existence."""
        row, ok = chat(cid)
        if row is None:
            return None, False
        found = None
        get = getattr(store, "message", None)
        if ok and callable(get):
            try:
                found = get(cid, mid)
            except Exception:  # noqa: BLE001 — a message that cannot be read is not shown
                found = None
        elif ok:
            found = {"id": mid}
        return ({"chat": row, "message": found} if found is not None or not ok else None), ok

    def meeting(mid):
        try:
            row = meeting_access(subject, mid, within=within)
            if row is None or not identity_token.is_shared(request.headers):
                return row
            # A TURN OTHER PEOPLE READ (the leak rule, as the chat lookup applies it): the meeting
            # is readable only when every person in the conversation may read it.
            readers = _shared_readers()
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

    def card(cid):
        """A card the viewer's own crm read returned, or nothing (agent-api cannot read crm)."""
        if cards is None:
            return None, refs_mod.UNAVAILABLE
        c = next((c for c in cards if isinstance(c, dict) and str(c.get("id")) == cid), None)
        return (c, refs_mod.READABLE) if c else (None, refs_mod.UNAVAILABLE)

    def person(pid):
        """A person the viewer shares a workspace with (or the viewer), by subject or email."""
        key = str(pid or "").strip().lower()
        if key in {subject.lower(), (request.headers.get("x-user-email") or "").strip().lower()}:
            return {"name": request.headers.get("x-user-email") or subject, "you": True}, refs_mod.READABLE
        mindex = d.get("mindex")
        try:
            from control_plane import workspace_membership as _m  # noqa: PLC0415
            rows, _ = _m.reconciled_memberships(wsr.root, subject, mindex.list) if mindex is not None else ([], False)
            for rec in rows:
                wid = str(rec.get("workspace_id") or rec.get("id") or rec.get("slug") or "")
                if not wid or not within(str(rec.get("slug") or wid)):
                    continue
                try:
                    members = _m.read_members(wsr.root, wid)
                except Exception:  # noqa: BLE001
                    continue
                if not any(str(m.get("subject") or "") == subject for m in members):
                    continue
                for m in members:
                    if key in {str(m.get("subject") or "").lower(), str(m.get("email") or "").lower()}:
                        return {"name": m.get("name") or m.get("email") or m.get("subject")}, refs_mod.READABLE
        except Exception:  # noqa: BLE001 — a roster that cannot be read names nobody
            pass
        return None, refs_mod.NOT_YOURS

    def flow(name):
        """One of the viewer's own routines (flows scoped ``user:<subject>``), by its name."""
        from control_plane import routine_flows  # noqa: PLC0415
        if not routine_flows.configured():
            return None, refs_mod.UNAVAILABLE
        want = str(name or "").removeprefix(routine_flows.FLOW_ID_PREFIX).removeprefix("flow:")
        try:
            flows = routine_flows.FlowsClient().routines(subject)
        except Exception:  # noqa: BLE001 — flows did not answer: cannot tell
            return None, refs_mod.UNAVAILABLE
        for f in flows:
            if f.get("name") == want and f.get("scope") == f"user:{subject}" and f.get("status") != "retired":
                return {"name": f.get("title") or f["name"]}, refs_mod.READABLE
        return None, refs_mod.UNAVAILABLE

    return refs_mod.Seams(workspace=workspace, page=page, chat=chat, meeting=meeting, join=join,
                          message=message, card=card, person=person, flow=flow, page_workspace=page_workspace)


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    registry = d["workspace_registry"]
    store = d.get("session_store")
    notifier = d.get("notifier")

    def _seams(request: Request, subject: str) -> refs_mod.Seams:
        return seams_for(request, subject, d)

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

    @router.post("/api/refs/audience")
    def ref_audience(request: Request, body: AudienceBody = Body(...)):
        """BEFORE YOU SHARE (D19: Share sends a reference; it never grants). Who in a room — or one
        person — could NOT open this reference with their own access. Answered only to someone who
        can open it themselves, and only as first names: never what the object is, and never a grant.
        The Share dialog shows one quiet line from it ("Maya can't open this: it's in your desk")."""
        subject = subject_of(request)
        answer = _resolve(request, body.ref)
        if answer["access"] not in (refs_mod.READABLE, refs_mod.GRANT):
            raise HTTPException(status_code=404, detail="nothing by that reference")
        people: list[str] = []
        if body.room:
            if store is None or store.get(body.room) is None or not store.can_write(body.room, subject):
                raise HTTPException(status_code=404, detail="no such room")
            people = [p for p in store.humans(body.room) if p != subject]
        elif body.person:
            people = [body.person]
        cannot = [p for p in people if not _can_open(answer, p)]
        where = "your desk" if answer["kind"] in ("chat", "message") else "a place they are not in"
        if answer["kind"] in ("page", "file", "entity", "workspace"):
            wid = str(answer["id"]).split("/", 1)[0]
            rec = registry.get(wid) or registry.by_slug(wid) or {}
            where = f"{rec.get('name')}" if rec.get("name") else "a workspace they are not in"
        return {"cannot": [_first_name(p) for p in cannot][:20], "cannot_count": len(cannot),
                "where": where, "everyone": not cannot}

    def _first_name(who: str) -> str:
        w = str(who or "")
        return (w.split("@", 1)[0].split(".")[0] or w).capitalize()

    def _can_open(answer: dict, who: str) -> bool:
        """The object's own access decision, asked for another person (never through this request's
        ceiling — that is the sharer's)."""
        k, i = answer["kind"], str(answer["id"])
        try:
            if k in ("chat", "room", "message"):
                cid = i.split("/", 1)[0]
                return bool(store is not None and store.can_read(cid, who))
            if k == "workspace":
                rec = registry.get(i) or registry.by_slug(i)
                return ids_mod.access_for(rec, who, root=d["wsr"].root, is_member=d["_ws_is_member"]) == ids_mod.ACCESS_READABLE
            if k in ("page", "entity", "file"):
                wid, _, target = i.partition("/")
                if k == "file":
                    return False
                a = link_resolver_mod.resolve(f"ws:{wid}/{target}", subject=who, root=d["wsr"].root,
                                              registry=registry, is_member=d["_ws_is_member"], within=lambda ws: True)
                return a.get("access") == refs_mod.READABLE
            if k == "meeting":
                look = meeting_access_check(d["_meeting_owner_lookup"], d["wsr"].root)
                return look(who, int(i), within=lambda ws: True) is not None
        except Exception:  # noqa: BLE001 — cannot tell: say they may not
            return False
        return True                     # a query, a person chip: each reader sees their own version

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
        if k in ("chat", "room", "message"):
            i = i.split("/", 1)[0] if k == "message" else i
            row = store.get(i) if store is not None else None
            return (row or {}).get("owner"), "chat", i, (row or {}).get("title") or ""
        wid = i.split("/", 1)[0]
        rec = registry.get(wid) or {}
        if k == "workspace":
            return rec.get("owner"), "workspace", i, rec.get("name") or ""
        return rec.get("owner"), "page", i, Path(i.split("/", 1)[-1]).stem

    return router
