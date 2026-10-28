"""pins.py — shared pins over HTTP (UI decision 7ee; PRD 0.13.4 §6 "Pins"; `workspace_pins`).

``GET /api/pins?workspace=&room=`` answers the workspace's pins (or one room's), each resolved for
the person asking through the one resolver: a pin they may open carries its label, one they may
not is ``restricted`` with no name (shot 91a). Who pinned it and when are shared with everyone there.

``POST /api/pins {workspace, room?, ref, pinned}`` pins or unpins for everyone. A workspace pin
takes a contributor (or the owner of the person's own desk); a room pin takes someone who may write
in that room. A pin is a reference only; it never grants (D18). Person only.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import universal_refs as refs_mod
from control_plane import workspace_membership as membership_mod
from control_plane import workspace_pins
from control_plane.ceiling import refuse_delegated, require_read_in_ceiling
from control_plane.commit_trailers import writer_trailers
from control_plane.routers import refs as refs_router
from control_plane.workspace_membership import MembershipError
from workspaces.shared import links

DESK = "personal"


class PinBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: str = Field(default=DESK, max_length=200)
    room: Optional[str] = Field(default=None, max_length=128, description="a room's session id: pin in that room")
    ref: str = Field(min_length=1, max_length=2000, description="a [[kind:id]] chip or a /r/<kind>/<id> link")
    pinned: bool = True


def build(**d) -> APIRouter:
    router = APIRouter()
    subject_of = d["subject_of"]
    wsr = d["wsr"]
    store = d.get("session_store")
    _read_target = d["_read_target"]

    def _target(request: Request, slug: str, *, write: bool):
        if slug != DESK:
            require_read_in_ceiling(request, slug)
        try:
            return _read_target(request, None if slug == DESK else slug, write=write)
        except HTTPException:
            raise
        except (ValueError, OSError):
            raise HTTPException(status_code=404, detail="no such workspace among yours") from None

    def _room_ok(slug: str, room: str, subject: str, *, write: bool) -> str:
        """The room's store id (a person's own room may be named by its chat name), or 404."""
        if store is None:
            raise HTTPException(status_code=404, detail="no such room")
        row = store.get(room) or store.by_name(subject, room)
        rid = row["id"] if row is not None else ""
        ok = row is not None and (store.can_write(rid, subject) if write else store.can_read(rid, subject))
        if not ok:
            raise HTTPException(status_code=404, detail="no such room")
        return rid

    def _resolved(request: Request, subject: str, pins: list[dict]) -> list[dict]:
        seams = refs_router.seams_for(request, subject, d)
        out = []
        for p in pins:
            u = links.parse_universal(p["ref"])
            a = refs_mod.resolve_ref(u, seams) if u is not None else {"access": "gone"}
            row = {"ref": p["ref"], "by": p.get("by") or "", "at": p.get("at")}
            if a.get("access") in (refs_mod.READABLE, refs_mod.GRANT):
                row.update({"access": "readable", "label": a.get("label"), "open": a.get("open"), "url": a.get("url")})
            else:
                row["access"] = "restricted"
            out.append(row)
        return out

    @router.get("/api/pins")
    def pins_read(request: Request, workspace: str = Query(default=DESK, max_length=200),
                  room: Optional[str] = Query(default=None, max_length=128)):
        """The pins of a workspace (or of one room in it), each resolved for you."""
        if workspace != DESK:
            require_read_in_ceiling(request, workspace)
        refuse_delegated(request, reason="person_only", instruction="Pins are read by the person's own app.")
        subject = str(subject_of(request))
        ws = _target(request, workspace, write=False)
        if room:
            room = _room_ok(workspace, room, subject, write=False)
        return {"workspace": workspace, "room": room, "pins": _resolved(request, subject, workspace_pins.read(ws, room))}

    @router.post("/api/pins")
    def pins_set(request: Request, body: PinBody = Body(...)):
        """Pin (or unpin) a reference for everyone in the workspace or the room."""
        if body.workspace != DESK:
            require_read_in_ceiling(request, body.workspace)     # the ceiling answers first, like every resolver
        refuse_delegated(request, reason="person_only", instruction=(
            "Pinning is the person's own act. Tell them where the Pin action is."))
        subject = str(subject_of(request))
        room = None
        if body.room:
            ws = _target(request, body.workspace, write=False)
            room = _room_ok(body.workspace, body.room, subject, write=True)
        else:
            if body.workspace != DESK:
                try:
                    membership_mod.require_role(wsr.root, body.workspace, subject, "contributor")
                except MembershipError:
                    raise HTTPException(status_code=403, detail={
                        "reason": "contributor_required",
                        "said": "Only contributors can pin here; readers see the pins."}) from None
            ws = _target(request, body.workspace, write=True)
        try:
            pins = workspace_pins.set_pin(ws, body.ref, by=subject, pinned=body.pinned, room=room,
                                          trailers=writer_trailers(request.headers))
        except workspace_pins.PinError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"workspace": body.workspace, "room": room, "pins": _resolved(request, subject, pins)}

    return router
