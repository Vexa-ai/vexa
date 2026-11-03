"""routers/rooms.py — a chat with two or more people is a room (`control_plane/rooms.py`).

    POST  /api/rooms                        make a room in a workspace: named, open (default) or closed
    GET   /api/sessions/{session}/room      what the room is: visibility, proactivity, the brief
    PATCH /api/sessions/{session}/room      a member changes proactivity, visibility or the brief
    POST  /api/rooms/{session}/join         an OPEN room's self-join (members of its workspace)
    GET   /api/rooms                        the rooms a person can see in a workspace
    GET   /api/rooms/list                   the same rooms described for a page or an agent: people by
                                            name, a live call, recurring meeting links (`rooms_list`)
    GET   /internal/chats/{session}/listen  the room's brief, for the signals service (internal tier)

ROOMS ARE MADE IN A WORKSPACE (founder 2026-10-15): `POST /api/rooms`, open by default; chats stay
private. A closed room's members invite (`POST /api/sessions/{s}/participants`, rooms on). Every act on
a room is a person's: an agent acting for someone with nobody watching may not make, join or
reconfigure one. All of it is behind the `rooms` feature (off by default). A change of proactivity or visibility is said in the room, by name.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import identity_token, meeting_listen, people_directory, rooms, rooms_overview, session_sync
from control_plane.bodies import CHAT_SESSION_PATTERN, ListenBrief
from control_plane.ceiling import is_unwatched, refuse_delegated, require_in_ceiling, require_read_in_ceiling

logger = logging.getLogger("agent_api.rooms")


class RoomPatchBody(BaseModel):
    """What a member changes about a room. Fields left out stay as they are."""
    model_config = ConfigDict(extra="forbid")
    proactivity: Optional[Literal["off", "quiet", "active"]] = Field(
        None, description="how readily Vexa speaks up here: off, quiet (when addressed or it clearly "
                          "matters) or active")
    visibility: Optional[Literal["open", "closed"]] = Field(
        None, description="open: its workspace's members see it and join; closed: members only")
    listen: Optional[ListenBrief] = Field(None, description="the room sensor's brief; the agent's to tune")
    level: Optional[str] = Field(None, max_length=200,
                                 description="the room's model (a catalog id), one for everybody in it; "
                                             "empty string = the deployment default")
    workspace: Optional[str] = Field(None, max_length=200,
                                     description="opening it: the workspace whose members will see it "
                                                 "(you must be a member); defaults to the room's own")
    confirm_history: Optional[bool] = Field(
        None, description="opening a closed room shows its history to every member of its workspace; "
                          "true once the person has been told who that is")


class RoomCreateBody(BaseModel):
    """A new room in a workspace."""
    model_config = ConfigDict(extra="forbid")
    workspace: str = Field(min_length=1, max_length=200, description="the workspace the room lives in")
    name: str = Field(min_length=1, max_length=120, description="the room's name")
    visibility: Literal["open", "closed"] = Field(
        "open", description="open (default): every member of the workspace sees it and joins; "
                            "closed: only its members see it")
    members: Optional[list[str]] = Field(None, max_length=50,
                                         description="account ids to add now; each a member of the workspace")


class PlanCallBody(BaseModel):
    """A call this room plans: when, who, and the agenda page."""
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300, description="what the call is called")
    start: str = Field(min_length=10, max_length=40, description="ISO 8601 start, with a zone")
    end: Optional[str] = Field(None, max_length=40, description="ISO 8601 end (default: 30 minutes)")
    invitees: Optional[list[str]] = Field(None, max_length=100, description="email addresses to invite")
    agenda: Optional[str] = Field(None, max_length=512, description="the agenda page's path in the room's workspace")


class CalendarSyncBody(BaseModel):
    """A change the calendar sensor read on the owner's event (PLAN-8H 8.4)."""
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=1024)
    status: Literal["updated", "cancelled"]
    start: Optional[str] = Field(default=None, max_length=40)
    end: Optional[str] = Field(default=None, max_length=40)


class CalendarRefBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_ref: str = Field(min_length=1, max_length=512,
                           description="the calendar event the creator's click made (its id)")


class HandOnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(min_length=1, max_length=200, description="the member who becomes the room's owner")


class WorkspaceConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: str = Field(min_length=1, max_length=200, description="the open workspace to connect")


class WorkspaceVisibilityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["open", "closed"] = Field(description="open: other workspaces' rooms may connect it")


def build(**d) -> APIRouter:
    router = APIRouter()
    store = d.get("session_store")
    subject_of = d["subject_of"]
    wsr = d.get("wsr")
    redis_url = d.get("redis_url")
    _internal_caller = d.get("_internal_caller")
    _global_root = d.get("_global_root")
    settings = d.get("settings")
    mindex = d.get("mindex")
    _schedule_source = d.get("_schedule_source")

    def _store():
        if store is None:
            raise HTTPException(status_code=503, detail={"refused": "session_store_off",
                                                         "note": "rooms need the session store"})
        return store

    def _is_writer(ws: str, who: str) -> bool:
        """Owner or contributor of ``ws``: who may make a room there, move a room into it, or choose
        the model its owner pays for. A reader takes part; they do not run the workspace's agent."""
        if wsr is None or not ws:
            return False
        from control_plane import room_principal as _rp_w, workspace_membership  # noqa: PLC0415
        try:
            return workspace_membership.is_member(wsr.root, ws, who) in _rp_w.WRITER_ROLES
        except Exception:  # noqa: BLE001 — an unreadable member list is not a writer
            return False

    def _is_member(ws: str, who: str) -> bool:
        if wsr is None or not ws:
            return False
        from control_plane import workspace_membership  # noqa: PLC0415
        try:
            return workspace_membership.is_member(wsr.root, ws, who) is not None
        except Exception:  # noqa: BLE001 — an unreadable member list is not a membership
            return False

    def _row(request: Request, session: str) -> tuple[str, dict]:
        subject = str(subject_of(request))
        row = session_sync.resolve(_store(), subject, session, create=False)
        if row is None:
            # a closed room is invisible to non-members: the same answer as one that does not exist
            raise HTTPException(status_code=404, detail="session not found")
        return subject, row

    def _person_only(request: Request) -> None:
        if is_unwatched(request):
            refuse_delegated(request, reason="room_needs_a_person",
                             instruction="Rooms are changed by the people in them. Say what you would "
                                         "change and stop.")

    def _policy_change(request: Request, workspace_id: str, role) -> None:
        """A change to a workspace's POLICY (visibility, connections) goes through the one evaluator
        (``policy_decide``, D7). INTERIM until S5's signed card: the workspace's owner, from a SESSION
        credential (D13) — no delegation token of any regime, no API key whatever its scopes. A store that
        cannot be read refuses (503, FD3)."""
        from control_plane import policy_decide as _pd  # noqa: PLC0415
        out = _pd.decide(_pd.Ask(principal=str(subject_of(request)), action="policy.change",
                                 workspace=workspace_id, role=role,
                                 credential=_pd.credential_of(request.headers)),
                         root=wsr.root)
        if out.decision == _pd.REFUSE:
            raise HTTPException(status_code=503, detail={"refused": _pd.POLICY_UNREADABLE, "rule": out.rule})
        if not out.allowed:
            raise HTTPException(status_code=403, detail={
                "refused": "policy_change_needs_the_owner_in_a_browser_session", "rule": out.rule,
                "note": "Visibility and connections are changed by the workspace's owner, from the app. "
                        "Say what you would change and stop."})

    def _say(row: dict, text: str) -> None:
        try:
            _store().add_note(row["id"], rooms.AGENT_AUTHOR, text)
        except Exception as exc:  # noqa: BLE001 — the change happened; its line is the extra
            logger.warning("room line not posted (%s)", type(exc).__name__)

    @router.get("/api/sessions/{session}/room")
    def room_get(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request):
        """What this chat is as a room: its people count, visibility, proactivity and brief."""
        _subject, row = _row(request, session)
        r = rooms.redis_for(redis_url)
        busy = rooms.agent_busy(r, row["id"])
        try:
            from control_plane import session_sync as _ss  # noqa: PLC0415
            held = bool(r is not None and r.get(_ss.lease_key(row["id"])))
        except Exception:  # noqa: BLE001
            held = busy
        return {"session_id": row["id"], **rooms.settings(_store(), row["id"]),
                "agent_busy": busy,
                # "thinking" from the moment a wake is accepted · "nothing" briefly when its turn
                # ended without a word · absent otherwise (the room's turn-status line)
                "agent_turn": rooms.turn_status(r, row["id"], busy=held)}

    @router.patch("/api/sessions/{session}/room")
    def room_patch(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request,
                   body: RoomPatchBody = Body(...)):
        """A member changes how the agent behaves here, who sees the room, or the room's brief. Any
        member may; a viewer may not. Proactivity and visibility changes are said in the room."""
        if body.workspace:
            require_in_ceiling(request, body.workspace)    # before anything else is looked at
        _person_only(request)
        subject, row = _row(request, session)
        s = _store()
        if not s.can_write(row["id"], subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        if not rooms.is_room(s, row["id"]):
            raise HTTPException(status_code=409, detail={
                "refused": "not_a_room", "note": "a chat is private; rooms are made in a workspace"})
        cur = rooms.settings(s, row["id"])
        listen = None
        if body.listen is not None:
            try:
                listen = meeting_listen.normalize(body.listen.model_dump(exclude_none=True),
                                                  base=cur["listen"] or meeting_listen.DEFAULT_BRIEF)
            except meeting_listen.BriefError as exc:
                raise HTTPException(status_code=422, detail={"refused": "brief_not_valid",
                                                             "said": str(exc)}) from None
        ws = cur["workspace"]
        if body.visibility == "open" and cur["visibility"] != "open":
            if body.workspace:
                if not _is_writer(body.workspace, subject):
                    raise HTTPException(status_code=403, detail={"refused": "needs_a_writer",
                                                                 "workspace": body.workspace})
                ws = body.workspace
            if not ws:
                raise HTTPException(status_code=409, detail={
                    "refused": "room_has_no_workspace", "note": "an open room is listed in a workspace"})
            if not body.confirm_history:
                # CLOSED → OPEN ASKS FIRST: who will now see the history
                raise HTTPException(status_code=409, detail={
                    "refused": "confirm_history",
                    "note": f"every member of {ws} will see this room and its history"})
        if body.level is not None and body.level != cur["level"] and not _is_writer(ws, subject):
            raise HTTPException(status_code=403, detail={"refused": "needs_a_writer", "workspace": ws})
        if body.level:
            from control_plane import model_providers  # noqa: PLC0415
            if not model_providers.is_model_id(body.level):
                raise HTTPException(status_code=422, detail={"refused": "not_a_model_id", "level": body.level})
        from control_plane import pair_rooms, room_membership  # noqa: PLC0415
        if body.visibility and body.visibility != cur["visibility"] and pair_rooms.is_pair_room(s, wsr.root, row["id"]):
            raise HTTPException(status_code=409, detail={"refused": "room_of_two",
                                                         "said": "A room of two is always just the two of you."})
        if body.visibility == "closed" and cur["visibility"] == "open" and ws:
            # OPEN → CLOSED (D36): the people who were following it stay, as explicit rows
            room_membership.seed(s, wsr.root, row["id"], keep=lambda who: _is_member(ws, who))
        out = s.put_room(row["id"], proactivity=body.proactivity, visibility=body.visibility,
                         listen=listen, workspace=ws or None, level=body.level)
        if body.visibility == "open" and cur["visibility"] != "open":
            room_membership.forget(wsr.root, row["id"])          # now derived from the workspace
        if body.level is not None and body.level != cur["level"]:
            _say(row, f"This room now runs on {body.level or 'the default model'} (from its next turn).")
        if body.proactivity and body.proactivity != cur["proactivity"]:
            _say(row, f"Vexa here is now {body.proactivity} ▾")
        if body.visibility and body.visibility != cur["visibility"]:
            _say(row, "This room is now open to the workspace." if body.visibility == "open"
                 else "This room is now closed: only its members see it.")
        return {"session_id": row["id"], **rooms.settings(s, row["id"]),
                "listen_version": out["listen_version"]}

    @router.post("/api/rooms", status_code=201)
    def room_create(request: Request, body: RoomCreateBody = Body(...)):
        """MAKE A ROOM in a workspace you are a member of — open by default. You own it."""
        require_in_ceiling(request, body.workspace)
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="A room is made by a person. Suggest it and stop.")
        subject = str(subject_of(request))
        s = _store()
        ws = body.workspace.strip()
        if not _is_member(ws, subject):
            raise HTTPException(status_code=403, detail={"refused": "not_a_workspace_member"})
        if not _is_writer(ws, subject):
            raise HTTPException(status_code=403, detail={"refused": "needs_a_writer", "workspace": ws})
        members = [m for m in dict.fromkeys(body.members or []) if m and m != subject]
        stranger = [m for m in members if not _is_member(ws, m)]
        if stranger:
            raise HTTPException(status_code=403, detail={"refused": "not_a_workspace_member",
                                                         "who": stranger})
        from control_plane import pair_rooms, room_membership  # noqa: PLC0415
        if pair_rooms.is_pair(wsr.root, ws):
            raise HTTPException(status_code=409, detail={"refused": "room_of_two",
                                                         "said": "A room of two has its one room."})
        row = s.create(subject, title=body.name.strip(), workspaces=[ws])
        s.put_room(row["id"], visibility=body.visibility, proactivity=rooms.DEFAULT_PROACTIVITY,
                   workspace=ws, created_by=subject)
        # WHO IS IN IT (D36): an open room's members are its workspace's (the rows below only record
        # who was brought in); a closed room's are explicit registry rows, the maker's first
        if body.visibility == "closed":
            room_membership.seed(s, wsr.root, row["id"])
        for m in members:
            room_membership.add(s, wsr.root, row["id"], m, actor=subject)
        return {"session_id": row["id"], "name": row["title"], **rooms.settings(s, row["id"])}

    @router.post("/api/rooms/{session}/join")
    def room_join(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request):
        """Join an OPEN room of a workspace you are a member of. A closed room is not found."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Joining a room is the person's act. Suggest it and stop.")
        subject = str(subject_of(request))
        s = _store()
        row = s.get(session) if session.startswith(session_sync.STORE_ID_PREFIX) else None
        if row is None or not rooms.visible_to(s, row["id"], subject, is_member=_is_member):
            raise HTTPException(status_code=404, detail="session not found")
        # AN OPEN ROOM'S MEMBERS ARE ITS WORKSPACE'S (D36): "joining" is following it — the room is
        # in your rail from now on, and a call in it rings you (the ring window counts from here)
        if s.row_role(row["id"], subject) is not None:
            return {"session_id": row["id"], "joined": False, **rooms.settings(s, row["id"])}
        if not rooms.can_join(s, row["id"], subject, is_member=_is_member):
            raise HTTPException(status_code=404, detail="session not found")
        s.join(row["id"], subject)
        return {"session_id": row["id"], "joined": True, **rooms.settings(s, row["id"])}

    @router.post("/api/rooms/{session}/leave")
    def room_leave(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request):
        """Leave a room. Its owner hands it on first — to the next member, automatically, when there
        is one (said in the room); the last person in a room cannot leave it."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Leaving a room is the person's act. Suggest it and stop.")
        subject, row = _row(request, session)
        s = _store()
        if s.room(row["id"]) is None:
            raise HTTPException(status_code=409, detail={"refused": "not_a_room",
                                                         "said": "This is a private chat, not a room."})
        from control_plane.session_store import SessionDenied  # noqa: PLC0415
        from control_plane import pair_rooms as _pr  # noqa: PLC0415
        if _pr.is_pair_room(s, wsr.root, row["id"]):
            raise HTTPException(status_code=409, detail={
                "refused": "room_of_two", "said": "A room of two is left by muting it, not leaving it."})
        handed_to = ""
        if s.role_of(row["id"], subject) == "owner":
            nxt = next((p["principal"] for p in s.participants(row["id"])
                        if p["kind"] == "human" and p["principal"] != subject and p["role"] == "member"), "")
            if not nxt:
                raise HTTPException(status_code=409, detail={
                    "refused": "hand_the_room_on_first",
                    "said": "You are the room's only member who can own it, so you cannot leave it. "
                            "Add someone and hand it on first (POST /api/rooms/{session}/hand-on)."})
            s.hand_on(row["id"], subject, nxt)
            handed_to = nxt
        from control_plane import pair_rooms, room_membership  # noqa: PLC0415
        if handed_to:
            room_membership.set_owner(s, wsr.root, row["id"], subject, handed_to)
        try:
            # a closed room: its registry row goes; an open room: only the following (it is derived)
            left = room_membership.remove(s, wsr.root, row["id"], subject)
        except SessionDenied as exc:
            raise HTTPException(status_code=403, detail={
                "refused": exc.reason, "said": f"You could not leave this room ({exc.reason})."}) from None
        if handed_to:
            _say(row, f"The room was handed on to {handed_to} when its owner left.")
        return {"session_id": row["id"], "left": left, **({"handed_to": handed_to} if handed_to else {})}

    @router.post("/api/rooms/{session}/hand-on")
    def room_hand_on(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request,
                     body: HandOnBody = Body(...)):
        """The room's owner hands it on to another member, who becomes its owner."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Handing a room on is the owner's act. Suggest it and stop.")
        subject, row = _row(request, session)
        s = _store()
        if s.room(row["id"]) is None:
            raise HTTPException(status_code=409, detail={"refused": "not_a_room"})
        from control_plane.session_store import SessionDenied  # noqa: PLC0415
        try:
            s.hand_on(row["id"], subject, body.to)
        except SessionDenied as exc:
            said = {"not_session_owner": "Only the room's owner can hand it on.",
                    "not_a_member": "The room can be handed on only to one of its members."}.get(exc.reason, exc.reason)
            raise HTTPException(status_code=403, detail={"refused": exc.reason, "said": said}) from None
        from control_plane import room_membership  # noqa: PLC0415
        room_membership.set_owner(s, wsr.root, row["id"], subject, body.to)
        _say(row, f"The room was handed on to {body.to}.")
        return {"session_id": row["id"], "owner": body.to,
                "members": [{"principal": p["principal"], "role": p["role"]} for p in s.participants(row["id"])]}

    @router.get("/api/rooms/{session}/members")
    def room_members(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request):
        """Who is in the room, with their role — its members only (a closed room is not found)."""
        _subject, row = _row(request, session)
        s = _store()
        if s.room(row["id"]) is None:
            raise HTTPException(status_code=409, detail={"refused": "not_a_room"})
        from control_plane import room_membership  # noqa: PLC0415
        try:
            mem = room_membership.members(s, wsr.root, row["id"])
        except Exception:  # noqa: BLE001 — an unreadable roster names nobody (FD3)
            raise HTTPException(status_code=503, detail={"refused": "policy_unreadable"}) from None
        return {"session_id": row["id"],
                "members": [{"principal": x["principal"], "kind": x["kind"], "role": x["role"],
                             "derived": x["derived"]} for x in mem]}

    # ── a workspace's open state and its connected open workspaces (C1's read grant) ───────────

    def _ws_role(request: Request, ws: str) -> str:
        require_in_ceiling(request, ws)
        from control_plane import workspace_membership  # noqa: PLC0415
        try:
            return workspace_membership.is_member(wsr.root, ws, str(subject_of(request))) or ""
        except Exception:  # noqa: BLE001
            return ""

    @router.get("/api/workspace-links/{workspace_id}")
    def ws_connected(workspace_id: Annotated[str, Path(min_length=1, max_length=200)], request: Request):
        """The workspace's open state, and the open workspaces its rooms' agent reads (read-only)."""
        if not _ws_role(request, workspace_id):
            raise HTTPException(status_code=404, detail="workspace not found")
        from control_plane import room_principal as _rp  # noqa: PLC0415
        return {"workspace": workspace_id, "visibility": _rp.visibility(wsr.root, workspace_id),
                "connected": [{**r, "open": _rp.visibility(wsr.root, r["workspace"]) == "open"}
                              for r in _rp.connections(wsr.root, workspace_id)]}

    @router.post("/api/workspace-links/{workspace_id}")
    def ws_connect(workspace_id: Annotated[str, Path(min_length=1, max_length=200)], request: Request,
                   body: WorkspaceConnectBody = Body(...)):
        """Connect an OPEN workspace (one you can read) to this one, whose owner or contributor you
        are: its rooms' agent then reads it, read-only. A workspace that is not open is refused."""
        require_in_ceiling(request, workspace_id, body.workspace)   # the ceiling before anything else
        _person_only(request)
        role = _ws_role(request, workspace_id)
        if role not in ("owner", "contributor"):
            raise HTTPException(status_code=403, detail={"refused": "not_a_workspace_writer"})
        _policy_change(request, workspace_id, role)      # connections are policy (D5): the owner's
        require_in_ceiling(request, body.workspace)
        from control_plane import room_principal as _rp, workspace_membership  # noqa: PLC0415
        try:
            rows = _rp.connect(wsr.root, workspace_id, body.workspace, actor=str(subject_of(request)),
                               commit_fn=workspace_membership.policy_commit)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail={"refused": "not_open", "note": str(exc)}) from None
        except (ValueError, workspace_membership.MembershipError) as exc:
            raise HTTPException(status_code=422, detail={"refused": "not_connectable", "note": str(exc)}) from None
        return {"workspace": workspace_id, "connected": rows}

    @router.post("/api/workspace-links/{workspace_id}/remove")
    def ws_disconnect(workspace_id: Annotated[str, Path(min_length=1, max_length=200)], request: Request,
                      body: WorkspaceConnectBody = Body(...)):
        """Stop reading a connected workspace."""
        require_in_ceiling(request, workspace_id, body.workspace)   # the ceiling before anything else
        _person_only(request)
        role = _ws_role(request, workspace_id)
        if role not in ("owner", "contributor"):
            raise HTTPException(status_code=403, detail={"refused": "not_a_workspace_writer"})
        _policy_change(request, workspace_id, role)
        from control_plane import room_principal as _rp, workspace_membership  # noqa: PLC0415
        return {"workspace": workspace_id,
                "connected": _rp.disconnect(wsr.root, workspace_id, body.workspace,
                                            commit_fn=workspace_membership.policy_commit)}

    @router.put("/api/workspace-links/{workspace_id}/visibility")
    def ws_visibility(workspace_id: Annotated[str, Path(min_length=1, max_length=200)], request: Request,
                      body: WorkspaceVisibilityBody = Body(...)):
        """Open a workspace (other workspaces' rooms may connect it and read it) or close it. Owner only."""
        require_in_ceiling(request, workspace_id)       # the ceiling before anything else
        _person_only(request)
        role = _ws_role(request, workspace_id)
        if role != "owner":
            raise HTTPException(status_code=403, detail={"refused": "not_the_workspace_owner"})
        _policy_change(request, workspace_id, role)
        from control_plane import room_principal as _rp, workspace_membership  # noqa: PLC0415
        return {"workspace": workspace_id,
                "visibility": _rp.set_visibility(wsr.root, workspace_id, body.visibility,
                                                 commit_fn=workspace_membership.policy_commit)}

    # ── the folders migration (J1): folders that hold work map to workspaces, the rest to Hidden ──

    def _folder_target(subject: str):
        sess = d.get("sess")
        return lambda session: (sess.target(subject, session) if sess is not None else "")

    @router.post("/api/rooms/calendar-sync")
    def room_calendar_sync(request: Request, body: CalendarSyncBody = Body(...)):
        """SYNC BACK (PLAN-8H 8.4) — internal tier only. The flows calendar sensor read a change to the
        caller's (`X-User-Id`) calendar event; their room calls whose recorded event it is follow it on
        meeting-api: the new time, or marked cancelled. Reads no calendar and tells nobody."""
        if _internal_caller is None or not _internal_caller(request):
            raise HTTPException(status_code=403, detail={"refused": "internal_tier_required"})
        subject = str(subject_of(request))
        from control_plane import room_calls  # noqa: PLC0415
        base, secret = _meeting_api()
        code, out = room_calls.sync(base, secret, subject=subject, body=body.model_dump(exclude_none=True))
        if not 200 <= int(code or 0) < 300 or not isinstance(out, dict):
            raise HTTPException(status_code=502 if not code else int(code), detail={"refused": "not_synced", "meetings": out})
        # THE ROOM SAYS IT (8.4): one line from Vexa in each room whose call the calendar moved or
        # cancelled — only in a room this owner may write, never anywhere else.
        st = _store()
        for ch in out.get("changes") or []:
            room_id = str((ch or {}).get("room") or "")
            if not room_id or st.room(room_id) is None or not st.can_write(room_id, subject):
                continue
            title = str(ch.get("title") or "Call")[:200]
            text = (f"Call cancelled in the organiser's calendar: {title}." if ch.get("status") == "cancelled"
                    else f"Call moved in the organiser's calendar: {title} — now {ch.get('start') or 'at a new time'}.")
            _say({"id": room_id}, text)
        return {"synced": list(out.get("synced") or [])}

    @router.post("/api/rooms/migrate-folders")
    def migrate_folders(request: Request, dry_run: Annotated[bool, Query()] = True):
        """Your chat folders, reorganised by workspace: a folder whose chats all write to one
        workspace shows under it; the rest go to Hidden. ``dry_run`` (the default) only reports.
        Nothing is deleted — the folders are kept, empty."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Reorganising a person's folders is theirs to do. Suggest it and stop.")
        from control_plane import folder_migration  # noqa: PLC0415
        subject = str(subject_of(request))
        s = _store()
        if dry_run:
            return {"dry_run": True, **folder_migration.plan(s, subject, target_of=_folder_target(subject))}
        notifier = d.get("notifier")
        note = (lambda text: notifier.emit([subject], kind="folders_migrated", actor="vexa",
                                           actor_name="Vexa", object_kind="none", object_id="folders",
                                           object_name=text)) if notifier is not None else None
        return {"dry_run": False,
                **folder_migration.apply(s, subject, target_of=_folder_target(subject), notify=note)}

    @router.get("/internal/rooms/folders-report")
    def folders_report(request: Request):
        """THE DRY-RUN REPORT for every person with folders — internal tier only, reads nothing back
        into the store: what each folder would become."""
        if _internal_caller is None or not _internal_caller(request):
            raise HTTPException(status_code=403, detail={"refused": "internal_tier_required"})
        from control_plane import folder_migration  # noqa: PLC0415
        s = _store()
        with s._lock:
            owners = [r[0] for r in s._x("SELECT DISTINCT owner FROM agent_session_filing").fetchall()]
        return {"people": [folder_migration.plan(s, o, target_of=_folder_target(o)) for o in owners]}

    # ── a room plans a call (F1): meeting-api's row, the creator's calendar the source of truth ──

    def _meeting_api() -> "tuple[str, str]":
        base = (getattr(settings, "meeting_api_url", "") or "") if settings is not None else ""
        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        if not base:
            raise HTTPException(status_code=503, detail={"refused": "meetings_not_deployed"})
        return base, secret

    @router.post("/api/rooms/{session}/plan", status_code=201)
    def room_plan(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request,
                  body: PlanCallBody = Body(...)):
        """PLAN A CALL IN THIS ROOM: a planned meeting the room owns (no bot), with its time, invitees
        and agenda. Nothing is sent: the answer carries the event for your own calendar to send on
        your click (`calendar_draft`), and the `.ics` for when no calendar is connected."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Planning a call that invites people is the person's act. Suggest "
                                     "it and stop.")
        subject, row = _row(request, session)
        s = _store()
        room = s.room(row["id"])
        if room is None:
            raise HTTPException(status_code=409, detail={"refused": "not_a_room"})
        if not s.can_write(row["id"], subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        if room.get("workspace"):
            from control_plane import workspace_membership  # noqa: PLC0415
            if (workspace_membership.is_member(wsr.root, room["workspace"], subject) or "") not in ("owner", "contributor"):
                raise HTTPException(status_code=403, detail={"refused": "not_a_workspace_writer"})
        from control_plane import room_calls  # noqa: PLC0415
        base, secret = _meeting_api()
        code, out = room_calls.plan(base, secret, subject=subject, room=room, session_id=row["id"],
                                    title=body.title, start=body.start, end=body.end or "",
                                    invitees=body.invitees, agenda=body.agenda or "")
        if not 200 <= int(code or 0) < 300 or not isinstance(out, dict):
            raise HTTPException(status_code=502 if not code else int(code),
                                detail={"refused": "meeting_not_planned", "meetings": out})
        _say(row, f"Call planned: {body.title} — {body.start}.")
        return {"session_id": row["id"], "meeting": out,
                "calendar_draft": room_calls.calendar_draft(out, room_title=row.get("title") or ""),
                "ics": f"/meetings/{out.get('id')}/ics"}

    @router.post("/api/rooms/{session}/plan/{meeting_id}/calendar")
    def room_plan_calendar(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)],
                           meeting_id: Annotated[int, Path(gt=0)], request: Request,
                           body: CalendarRefBody = Body(...)):
        """Record the calendar event the creator's own click made: from now on the calendar is the
        source of truth for the call's time and invitees."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Only the person who made the event records it. Stop.")
        subject, row = _row(request, session)
        if _store().room(row["id"]) is None:
            raise HTTPException(status_code=409, detail={"refused": "not_a_room"})
        from control_plane import room_calls  # noqa: PLC0415
        base, secret = _meeting_api()
        code, out = room_calls.record_event(base, secret, subject=subject, meeting_id=meeting_id,
                                            event_ref=body.event_ref)
        if not 200 <= int(code or 0) < 300 or not isinstance(out, dict):
            raise HTTPException(status_code=502 if not code else int(code),
                                detail={"refused": "event_not_recorded", "meetings": out})
        if (out.get("data") or {}).get("room") != row["id"]:
            raise HTTPException(status_code=404, detail="no planned call of this room")
        return {"session_id": row["id"], "meeting": out}

    @router.get("/api/rooms")
    def room_list(request: Request, workspace: Annotated[str, Query(min_length=1, max_length=200)]):
        """The rooms of a workspace this person can see: the open ones (for its members) and the
        ones they are in. A closed room they are not in is not listed."""
        subject = str(subject_of(request))
        return {"workspace": workspace,
                "rooms": rooms_overview.visible_rooms(_store(), workspace, subject, is_member=_is_member)}

    @router.get("/api/rooms/list")
    def room_overview(request: Request, workspace: Annotated[str, Query(min_length=1, max_length=200)]):
        """THE ROOMS OF A WORKSPACE, DESCRIBED (held-label-rooms-1018): every room this reader may see
        there — the same rule as ``GET /api/rooms`` — with its people by name, whether a call is live,
        and the recurring meetings bound to it. A turn other people read (a room's agent, a shared
        chat) is held to the workspace it was dispatched into and sees its OPEN rooms only, with no
        meetings; a workspace's own agent names no other workspace."""
        require_read_in_ceiling(request, workspace)        # before anything is looked at
        hdrs = request.headers
        principal = identity_token.is_workspace_principal(hdrs)
        if principal:
            own = (hdrs.get(identity_token.DELEGATION_HEADERS["principal_workspace"]) or "").strip()
            if not own or workspace != own:
                raise HTTPException(status_code=403, detail={
                    "refused": "out_of_scope", "workspace": workspace,
                    "why": "a workspace's agent lists only its own workspace's rooms"})
        shared = principal or identity_token.is_shared(hdrs)
        subject = str(subject_of(request))
        if wsr is None:
            raise HTTPException(status_code=503, detail={"refused": "workspaces_off"})

        def _index_list(who: str) -> list:
            return list(mindex.list(who) or []) if mindex is not None else []

        def _name(who: str) -> str:
            try:
                return people_directory.name_of(wsr.root, str(who), _index_list)
            except Exception:  # noqa: BLE001 — a name is a courtesy
                return ""

        meeting_rows = None
        if not shared and callable(_schedule_source):
            meeting_rows = lambda: _schedule_source(subject)  # noqa: E731 — the reader's own meetings
        return rooms_overview.overview(_store(), wsr.root, workspace, subject, shared=shared,
                                       is_member=_is_member, name_of=_name, meeting_rows=meeting_rows)

    @router.get("/internal/chats/{session}/listen")
    def chat_listen_internal(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request,
                             subject: Annotated[str, Query(min_length=1, max_length=200)]):
        """THE ROOM'S BRIEF, FOR `signals` — internal tier only, the answer shape of a meeting's
        (`GET /internal/meetings/{row}/listen`): the room's brief (the company default when it has
        none), version, the chat a wake lands in, the signal policy — plus proactivity, visibility
        and how many people. ``subject`` is the room's owner; a room they are not in is a 404."""
        if _internal_caller is None or not _internal_caller(request):
            raise HTTPException(status_code=403, detail={"refused": "internal_tier_required"})
        s = _store()
        row = session_sync.resolve(s, subject, session, create=False)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        from control_plane import home as _home  # noqa: PLC0415
        try:
            root = _global_root() if callable(_global_root) else None
        except Exception:  # noqa: BLE001
            root = None
        pol = meeting_listen.policy(_home.read_global(root, "POLICIES.md"), subject=subject)
        st = rooms.settings(s, row["id"])
        # what the people chose (proactivity) applied to what the agent wrote (the brief)
        brief = rooms.effective_brief(st["listen"] or meeting_listen.default_brief(pol), st["proactivity"])
        if not st.get("agent_on", True):
            brief = {**brief, "labels": [{**lab, "wake": False} for lab in (brief.get("labels") or [])]}
        return {"brief": brief, "version": st["listen_version"], "default": not st["listen"],
                "workspace": st["workspace"], "page_path": "", "chat_session": row["id"],
                "subject": subject, "room": st["room"], "humans": st["humans"],
                "visibility": st["visibility"], "proactivity": st["proactivity"],
                # "Vexa in this room" off (S4): the sensor stands down — nothing to score, nothing to wake
                "agent_on": st.get("agent_on", True),
                "stream": rooms.stream_key(row["id"]),
                "policy": {k: pol[k] for k in ("transcript_signals", "model_signals",
                                                "signals_wakes_per_meeting",
                                                "signals_cost_per_meeting_usd")}}

    return router
