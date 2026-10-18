"""routers/calls.py — a call held in a session, and what is decided after it (ADR-0048 decisions 6, 7).

A call is a meeting (meeting-api owns it: ``POST /bots {"platform": "vexa_call", "session_id"}``
creates its LiveKit room and the listener that transcribes it). The session is agent-api's, so
agent-api decides who joins:

* ``POST /api/sessions/{s}/call`` — the starter binds their call to the session. The room's own
  metadata, read back from the LiveKit server, must name the starter and this session; the body
  carries the transcript share the starter minted, which each participant redeems on joining.
* ``GET  /api/sessions/{s}/call`` — the session's live call, or ``null`` (a room that has closed is
  marked ended here).
* ``POST /api/sessions/{s}/call/token`` — a participant's room token, minted only for a participant
  of this session, for this session's room, short-lived. A viewer's token listens only. Joining
  gives the participant the call's transcript (the starter's share, redeemed as them).
* ``POST /api/sessions/{s}/messages`` — post a line into the session without an agent turn: the
  private side chat's *Share to the room*.
* ``GET  /api/sessions/{s}/proposals`` and ``POST …/proposals/{id}/accept|reject`` — the changes the
  post-meeting run proposed; only the meeting's owner decides, and nothing reaches a workspace
  until they accept. An accepted page is committed in the workspace with the owner as its author.
* ``POST /api/sessions/{s}/proposals`` — the internal tier (the post-meeting flow) records one.

Every route answers ``503`` without the session store, the call routes ``503`` without a LiveKit
server, and ``404`` for a session the caller is not in.
"""
from __future__ import annotations

import logging
from pathlib import Path as FsPath
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import identity_token, room_echo, session_access, session_sync, system_mounts
from control_plane import workspace_membership as membership_mod
from control_plane.bodies import CHAT_SESSION_PATTERN
from control_plane.ceiling import names_in_ceiling, refuse_delegated, require_in_ceiling
from control_plane.session_store import SessionDenied
from control_plane.livekit_rooms import room_ended
from control_plane.workspace_membership import MembershipError
from shared.gitexec import run_git
from workspaces.shared import workspace_paths as wpaths

logger = logging.getLogger("agent_api.calls")

NOT_CONFIGURED = "Shared sessions are not configured on this deployment"
NO_CALLS = "Calls are not deployed here: no LiveKit server is configured"
ROOM_PATTERN = r"^call-[a-z0-9-]{4,64}$"
SessionPath = Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)]


class BindCallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room: str = Field(pattern=ROOM_PATTERN, description="the call's room (its native_meeting_id)")
    meeting_id: Optional[int] = Field(default=None, gt=0,
                                      description="the call's meeting row; absent for an untranscribed call, which has none")
    share_token: Optional[str] = Field(default=None, min_length=8, max_length=512,
                                       description="a transcript share of the call, minted by its starter; absent when untranscribed")


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=8000)


class ShowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: str = Field(min_length=1, max_length=128, description="the shared workspace the page is in")
    path: str = Field(min_length=1, max_length=512, description="the page's path in that workspace")
    anchor: Optional[str] = Field(default=None, max_length=500,
                                  description="a heading or a quote on the page to scroll to and highlight")
    stop: Optional[bool] = Field(default=None, description="true to stop showing (the presenter only)")


class HighlightBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor: str = Field(min_length=1, max_length=500, description="a heading or a quote on the shown page")


#: Topic of the call's data messages for a shown page (the dock listens on it).
SHOW_TOPIC = "vexa.show"


class ProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str = Field(min_length=1, max_length=200, description="who decides it: the meeting's owner")
    kind: Literal["page", "entity", "commitment", "note"]
    body: str = Field(min_length=1, max_length=200_000)
    title: str = Field(default="", max_length=300)
    workspace: Optional[str] = Field(default=None, max_length=128)
    path: Optional[str] = Field(default=None, max_length=512)
    meeting_id: Optional[int] = None


class CallShareBody(BaseModel):
    """M8 (0.13.4 S4): the call's owner gives one person its transcript."""
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,199}$",
                         description="the person on this call to give its transcript to")


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Optional[str] = Field(default=None, max_length=200_000, description="the owner's edit")


def build(**d) -> APIRouter:
    router = APIRouter()
    store = d.get("session_store")
    subject_of = d["subject_of"]
    livekit = d.get("livekit")
    redeem_share = d.get("call_share_redeemer")
    _internal_caller = d["_internal_caller"]
    _read_target = d["_read_target"]
    wsr = d["wsr"]
    settings = d.get("settings")
    notifier = d.get("notifier")
    mindex = d.get("mindex")
    sess = d.get("sess")
    _meeting_annotator = d.get("_meeting_annotator")
    _meeting_titler = d.get("_meeting_titler")
    _global_root = d.get("_global_root")

    def _store():
        if store is None:
            raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
        return store

    def _livekit():
        if livekit is None:
            raise HTTPException(status_code=503, detail={"error": "calls_not_deployed", "message": NO_CALLS})
        return livekit

    def _row(request: Request, session: str, *, create: bool = False) -> tuple[str, dict]:
        s = _store()
        subject = subject_of(request)
        row = session_sync.resolve(s, subject, session, create=create)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        return subject, row

    def _room(name: str) -> Optional[dict]:
        try:
            return _livekit().room(name)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — the server unreachable is a 502, never a guess
            logger.warning("livekit room lookup failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="the call server did not answer") from None

    def _call_view(call: dict, room: dict) -> dict:
        meta = room.get("metadata") or {}
        return {"room": call["room"], "meeting_id": call["meeting_id"] or None, "owner": call["owner"],
                "url": _livekit().url, "started": call["started"],
                "transcribed": bool(meta.get("transcribed", True)),
                "participants": room.get("num_participants", 0),
                # who is in it now, by name (the listener is hidden and not here): the live row's faces
                "in_now": [{"subject": p.get("identity") or "", "name": p.get("name") or ""}
                           for p in (room.get("in_now") or [])][:12]}

    def _live_call(session_id: str) -> Optional[tuple[dict, dict]]:
        s = _store()
        while True:
            call = s.active_call(session_id)
            if call is None:
                return None
            room = _room(call["room"])
            # A ROOM WHOSE MEETING ENDED IS NOT THE LIVE CALL (live app.dev 2026-10-18): the room
            # outlives its meeting by its departure timeout, and rejoining it got a person a room with
            # no listener — the second start in the same chat transcribed nothing.
            if room is not None and not room_ended(room):
                return call, room
            s.end_call(session_id, call["room"])

    # ── the call ─────────────────────────────────────────────────────────────────────────────
    def _own_by_room(subject, row: dict, meeting_id) -> None:
        """A CALL STARTED IN A ROOM IS THE ROOM'S (rooms on, R1 1.2): its meeting row records the room
        and the room's workspace, and the meeting's own chat (`meet-<row>`, where the post-meeting
        recap is posted) is the room — so the recap lands in the room, run by the room's agent.
        Off, or outside a room, nothing changes. Never fails the bind."""
        if not meeting_id:
            return
        from control_plane import rooms  # noqa: PLC0415
        try:
            if not rooms.enabled(_global_root, str(subject)):
                return
            room = _store().room(row["id"])
            if room is None:
                return
            if sess is not None:
                sess.bind_meeting_chat(str(subject), str(meeting_id), row["id"])
            if _meeting_annotator is not None:
                _meeting_annotator(str(subject), meeting_id,
                                   {"room": row["id"], "workspace": room.get("workspace") or ""})
        except Exception as exc:  # noqa: BLE001 — the call is bound; its ownership record is the extra
            logger.warning("room ownership not recorded for call meeting %s (%s)", meeting_id,
                           type(exc).__name__)

    def _name_call(subject, session_id: str, meeting_id, email: str = "") -> None:
        """A CALL IS NAMED AFTER WHO OR WHERE (founder O1, 2026-10-18): "Call with Megan" in a room of
        two, "#pilot call" in a named room; a private chat's call keeps the meeting rule's name. Never
        fails the bind."""
        if not meeting_id or _meeting_titler is None:
            return
        from control_plane import call_names, pair_rooms, people_directory  # noqa: PLC0415
        try:
            base = call_names.base_for(
                _store(), session_id, str(subject),
                is_pair=lambda ws: pair_rooms.is_pair(wsr.root, ws),
                name_of=lambda who: people_directory.name_of(
                    wsr.root, str(who), mindex.list if mindex else (lambda _s: []),
                    email if str(who) == str(subject) else ""))
            if base is not None:
                call_names.name_call(_meeting_titler, str(subject), meeting_id, base)
        except Exception as exc:  # noqa: BLE001 — the call is bound; its name is furniture
            logger.warning("call %s not named (%s)", meeting_id, type(exc).__name__)

    @router.post("/api/sessions/{session}/call")
    def bind_call(session: SessionPath, body: BindCallBody, request: Request):
        """Bind the caller's call to this session. The room must exist and its metadata (written by
        meeting-api when it created the room) must name the caller as owner and this session."""
        subject, row = _row(request, session)
        if not _store().can_write(row["id"], subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        room = _room(body.room)
        meta = (room or {}).get("metadata") or {}
        if (room is None or str(meta.get("owner")) != str(subject) or meta.get("session_id") != row["id"]
                or meta.get("meeting_id") != body.meeting_id):
            raise HTTPException(status_code=404, detail="no such call of yours for this session")
        if body.meeting_id is None:
            # AN UNTRANSCRIBED CALL (a private or incognito chat's): no transcriber and no meeting,
            # so nothing to share. Only a room meeting-api made untranscribed binds this way.
            if meta.get("transcribed", True) is not False or body.share_token:
                raise HTTPException(status_code=422, detail="only an untranscribed call has no meeting")
        elif not body.share_token or not body.share_token.startswith(f"{body.meeting_id}."):
            raise HTTPException(status_code=422, detail="the share is not of this call's meeting")
        from control_plane import room_switches  # noqa: PLC0415
        if body.meeting_id is not None and not room_switches.transcribe_on(_store(), row["id"]):
            # "TRANSCRIBE CALLS" IS OFF HERE (UI 7hh, S4): a call in this room has audio only — no
            # transcript and no notes. The client starts it untranscribed; a transcribed one is refused.
            raise HTTPException(status_code=409, detail={
                "refused": "transcription_off_in_this_room",
                "said": "Calls in this room are not transcribed. Start it without a transcript, or ask to "
                        "turn transcription back on."})
        call = _store().bind_call(row["id"], room=body.room, meeting_id=body.meeting_id or 0, owner=str(subject),
                                  share_token=body.share_token or "")
        _own_by_room(subject, row, body.meeting_id)
        _name_call(subject, row["id"], body.meeting_id, request.headers.get("x-user-email") or "")
        rang: list = []
        if notifier is not None:
            try:
                from control_plane import people_directory  # noqa: PLC0415

                name = people_directory.name_of(wsr.root, str(subject), mindex.list if mindex else (lambda _s: []),
                                                request.headers.get("x-user-email") or "")
                if _store().room(row["id"]) is not None:
                    # A ROOM CALL RINGS THE ROOM'S MEMBERS, AND ONLY THEM (S4, D36; sharing-tagging §7):
                    # a live ring plus a notice that expires. Workspace members outside it see the
                    # live call on the workspace page (7l) — discovery, not an invitation.
                    from control_plane import room_ring  # noqa: PLC0415
                    rang = room_ring.ring(_store(), wsr.root, notifier, row["id"], caller=str(subject),
                                          caller_name=name, title=row.get("title") or "", call_room=body.room)
                else:
                    # ``call_started`` (#1783), from this route only: to every other person in the chat.
                    others = [h for h in _store().humans(row["id"]) if h != str(subject)]
                    if others:
                        notifier.emit(others, kind="call_started", actor=str(subject), actor_name=name,
                                      object_kind="chat", object_id=row["id"], object_name=row.get("title") or "a chat")
                    rang = others
            except Exception as exc:  # noqa: BLE001 — the call is bound; its notice is the extra
                logger.warning("call notification not sent (%s)", type(exc).__name__)
        out = {"session_id": row["id"], "call": _call_view(call, room), "rang": rang}
        if not rang and _store().room(row["id"]) is not None:
            # NOBODY ELSE IS HERE (the dogfood case): say so, and how to ring someone
            out["hint"] = "Nobody else is in this room yet. To ring someone, @mention or Invite them."
        return out

    @router.get("/api/sessions/{session}/call")
    def get_call(session: SessionPath, request: Request):
        """The session's live call, or null. Any participant may read it. A chat of the caller's own
        that has no record yet gets one here (as a first turn would): "Think out loud" starts a call
        in a new chat before anyone has typed in it (founder, 2026-10-14)."""
        subject, row = _row(request, session, create=True)
        live = _live_call(row["id"]) if livekit is not None else None
        # `you` lets the reader tell its own call from someone else's (the terminal joins a call the
        # agent started for this person only when `call.owner == you`). Their own id, nothing more.
        return {"session_id": row["id"], "calls": livekit is not None, "you": str(subject),
                "call": _call_view(*live) if live else None}

    @router.post("/api/sessions/{session}/call/token")
    def call_token(session: SessionPath, request: Request):
        """A room token for a participant of this session, for its call's room. A viewer listens
        only. Joining gives the participant the call's transcript (the starter's share)."""
        subject, row = _row(request, session)
        lk = _livekit()
        live = _live_call(row["id"])
        if live is None:
            raise HTTPException(status_code=404, detail="no call in this session")
        call, room = live
        role = _store().role_of(row["id"], subject)
        # JOINING A CALL NO LONGER INHERITS ITS TRANSCRIPT (security review M8, 0.13.4 S4): the starter's
        # share is given explicitly, per person (`POST …/call/share`). A room call's notes land in the
        # room, which its members already read.
        shared = False
        if str(subject) != call["owner"] and redeem_share is not None and call["share_token"]:
            if str(subject) in set(_store().call_shared_with(row["id"], call["room"])):
                email = request.headers.get("x-user-email", "")
                shared = bool(redeem_share(str(subject), email, call["share_token"]))
        name = ("Vexa" if identity_token.is_workspace_principal(request.headers)
                else (request.headers.get("x-user-email", "") or "").split("@", 1)[0] or str(subject))
        token = lk.participant_token(room=call["room"], identity=str(subject), name=name,
                                     can_publish=role in ("owner", "member"))
        return {"session_id": row["id"], "url": lk.url, "token": token, "role": role,
                "transcript_shared": shared, "call": _call_view(call, room)}

    @router.post("/api/sessions/{session}/call/share")
    def call_share(session: SessionPath, request: Request, body: CallShareBody):
        """THE CALL'S OWNER GIVES ONE PERSON ITS TRANSCRIPT (M8): explicit and per person, never by
        joining. Only a participant of this session, only by the call's owner, only a person's click."""
        refuse_delegated(request, reason="call_share_needs_a_person",
                         instruction="Sharing a call's transcript is its owner's click. Suggest it and stop.")
        subject, row = _row(request, session)
        live = _live_call(row["id"])
        if live is None:
            raise HTTPException(status_code=404, detail="no call in this session")
        call, _room_ = live
        if str(subject) != call["owner"]:
            raise HTTPException(status_code=403, detail="only the call's owner shares its transcript")
        if _store().role_of(row["id"], body.subject) is None:
            raise HTTPException(status_code=404, detail="person not found")
        if not call["share_token"]:
            raise HTTPException(status_code=409, detail="this call is not transcribed")
        _store().share_call_with(row["id"], call["room"], body.subject)
        return {"session_id": row["id"], "shared_with": body.subject}

    # ── show to everyone (founder 2026-10-15) ─────────────────────────────────────────────────
    #: Who is showing what, per session — EPHEMERAL (this process): never stored (`room_show`).
    from control_plane import room_show  # noqa: PLC0415

    def _send(room: str, payload: dict) -> None:
        send = getattr(_livekit(), "send_data", None)
        if send is None or not send(room, payload, topic=SHOW_TOPIC):
            raise HTTPException(status_code=502, detail="the call server did not take the message")

    def _presenter(request: Request, session: str) -> tuple[str, dict]:
        """Who shows in ``session``, and its row. A ROOM'S OWN AGENT runs as its workspace's principal
        (D2), an account that is in no session: it presents in the one room its unit answers in, and
        in no other. Everyone else must be able to write the session."""
        if identity_token.is_workspace_principal(request.headers):
            unit = identity_token.session_of(request.headers)
            row = _store().get(session) if room_echo.session_of(unit) == session else None
            if row is None or _store().room(row["id"]) is None:
                raise HTTPException(status_code=404, detail="session not found")
            return str(subject_of(request)), row
        subject, row = _row(request, session)
        if not _store().can_write(row["id"], subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        return subject, row

    @router.post("/api/sessions/{session}/call/show")
    def call_show(session: SessionPath, body: ShowBody, request: Request):
        """Show a page to everyone in this session's call: it opens in every participant's panel and
        follows the presenter. Only a page EVERY person in the chat can already read is shown — a
        page in a workspace someone is not in is refused with who is missing, never sent."""
        # a delegated dispatch names its workspaces: anything outside them is refused before all else
        require_in_ceiling(request, body.workspace.strip())
        subject, row = _presenter(request, session)
        live = _live_call(row["id"])
        # THE PRESENTER KEEPS CONTROL (JTBD-1): only they replace or stop what is shown; anyone else
        # closes their own panel. Without an in-app call the show is the ROOM's: every member's app
        # reads it through the room's messages and opens it.
        cur = room_show.current(row["id"])
        if cur and cur["by"] != str(subject):
            if body.stop:
                raise HTTPException(status_code=403, detail="only the person showing it can stop it")
            raise HTTPException(status_code=409, detail={"error": "someone_is_showing", "by": cur["by"],
                                                         "message": "Someone is showing a page here — wait until they stop."})
        if body.stop:
            room_show.stop(row["id"])
            if live is not None:
                _send(live[0]["room"], {"type": "stop", "by": str(subject)})
            return {"session_id": row["id"], "shown": None}
        humans = [str(h) for h in _store().humans(row["id"])]
        root = wsr.root
        ws = body.workspace.strip()
        # THE PLACE IS ONE OF THREE, by name: the caller's own desk, the company layer, or a shared
        # workspace the caller is a member of. Anything else (`.`, `..`, a path) is refused before a
        # roster or a file is read, so the answer never says what exists outside the caller's reach.
        own = ws in ("personal", "desk", "_global")
        if not own and not wpaths.is_workspace_name(ws, tier=False):
            raise HTTPException(status_code=422, detail="workspace is personal, _global or a workspace name")
        # A ROOM'S OWN AGENT (a workspace principal, D2) is in no roster: what it may show is exactly
        # the workspace its dispatch names. Everyone else must be a member.
        if identity_token.is_workspace_principal(request.headers):
            if not own and not names_in_ceiling(request, ws):
                raise HTTPException(status_code=403, detail="not a member of that workspace")
        elif not own and membership_mod.is_member(root, ws, str(subject)) is None:
            raise HTTPException(status_code=403, detail="not a member of that workspace")
        if own:
            missing = [h for h in humans if h != str(subject)]
        else:
            missing = [h for h in humans if membership_mod.is_member(root, ws, h) is None]
        if missing:
            names = {}
            try:
                from control_plane import people_directory  # noqa: PLC0415
                for h in missing:
                    names[h] = people_directory.name_of(root, h, mindex.list if mindex else (lambda _s: [])) or h
            except Exception:  # noqa: BLE001 — names are a courtesy; the refusal stands without them
                names = {h: h for h in missing}
            raise HTTPException(status_code=409, detail={
                "error": "not_shared", "workspace": ws,
                "missing": [{"subject": h, "name": names.get(h, h)} for h in missing],
                "message": "Not everyone in the call can open this page. Share its workspace with them first.",
            })
        # the desk and the company layer resolve through the workspace door; a shared workspace
        # lives at <root>/<id>; the page is read without following a link
        base = (_read_target(request, None if ws in ("personal", "desk") else ws) if own
                else FsPath(root) / ws)
        text = wpaths.read_text_inside(base, body.path, max_bytes=2_000_000)
        if text is None:
            raise HTTPException(status_code=404, detail="no such page in that workspace")
        title = body.path.rsplit("/", 1)[-1].removesuffix(".md")
        for line in text.splitlines()[:80]:
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"') or title
                break
            if line.startswith("# "):
                title = line[2:].strip() or title
                break
        payload = {"type": "show", "by": str(subject), "workspace": ws, "path": body.path, "title": title,
                   **({"anchor": body.anchor} if body.anchor else {})}
        if live is not None:
            _send(live[0]["room"], payload)
        room_show.show(row["id"], by=str(subject), workspace=ws, path=body.path, title=title)
        # THE ONE TRACE: a line in the chat (the meeting's history), linking the page.
        import time as _t  # noqa: PLC0415
        name = ("Vexa" if identity_token.is_workspace_principal(request.headers)
                else (request.headers.get("x-user-email", "") or "").split("@", 1)[0] or str(subject))
        verb = "showed" if live is not None else "is showing"
        try:
            _store().add_note(row["id"], str(subject),
                              f"{name} {verb} “{title}” · {_t.strftime('%H:%M', _t.gmtime())} UTC — {ws}/{body.path}")
        except Exception as exc:  # noqa: BLE001 — the page is shown; its line is the extra
            logger.warning("show line not written (%s)", type(exc).__name__)
        return {"session_id": row["id"], "shown": payload}

    @router.post("/api/sessions/{session}/call/highlight")
    def call_highlight(session: SessionPath, body: HighlightBody, request: Request):
        """Highlight a heading or quote on the page being shown, for everyone, and scroll it into
        view. Transient: the next highlight replaces it."""
        subject, row = _presenter(request, session)
        live = _live_call(row["id"])
        if live is None:
            raise HTTPException(status_code=404, detail="no call in this session")
        payload = {"type": "highlight", "by": str(subject), "anchor": body.anchor}
        _send(live[0]["room"], payload)
        return {"session_id": row["id"], "highlighted": payload}

    # ── share to the room ────────────────────────────────────────────────────────────────────
    @router.post("/api/sessions/{session}/messages")
    def post_message(session: SessionPath, body: MessageBody, request: Request):
        """Post one line into the session without an agent turn (a private chat's Share to the room).
        Every participant reads it; the agent hears it on its next turn."""
        subject, row = _row(request, session)
        if not _store().can_write(row["id"], subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        try:
            note = _store().add_note(row["id"], str(subject), body.text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"session_id": row["id"], "message": note}

    # ── owner review (ADR-0048 decision 7) ───────────────────────────────────────────────────
    @router.get("/api/sessions/{session}/proposals")
    def list_changes(session: SessionPath, request: Request):
        subject, row = _row(request, session)
        return {"session_id": row["id"], "you": subject, "proposals": _store().proposals(row["id"])}

    @router.post("/api/sessions/{session}/proposals")
    def propose_change(session: SessionPath, body: ProposalBody, request: Request):
        """Record a proposed change (the post-meeting flow, internal tier)."""
        if not _internal_caller(request):
            raise HTTPException(status_code=403, detail="proposals are recorded by the post-meeting run")
        s = _store()
        row = s.get(session)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        try:
            p = s.propose(row["id"], owner=body.owner, kind=body.kind, body=body.body, title=body.title,
                          workspace=body.workspace, path=body.path, meeting_id=body.meeting_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # AUTO-ACCEPT (policy `post_meeting_auto_accept`, default off): the change lands at once,
        # as its owner would have landed it — their write rights, their name on the commit. The
        # request must then carry the owner as its person (the internal tier's X-User-Id).
        if _auto_accept(request, p):
            commit = _land(request, body.owner, p, p["body"])
            p = s.decide(p["id"], body.owner, accept=True)
            return {"proposal": p, "commit": commit, "auto_accepted": True}
        return {"proposal": p}

    def _auto_accept(request: Request, p: dict) -> bool:
        if str(request.headers.get("x-user-id") or "") != p["owner"]:
            return False
        global_root = system_mounts.global_root(settings, wsr.root) if settings is not None else None
        ws_root = None
        if p["workspace"]:
            try:
                ws_root = _read_target(request, p["workspace"])
            except HTTPException:
                return False
        return session_access.auto_accept_policy(global_root, ws_root)

    def _land(request: Request, subject: str, p: dict, body: str) -> Optional[str]:
        """Write an accepted page or entity into its workspace AS the owner, and commit it in their
        name. The owner's own write rights apply: a workspace they cannot write refuses here."""
        if p["kind"] not in ("page", "entity"):
            return None
        slug = p["workspace"]
        require_in_ceiling(request, slug)
        if slug not in (subject, system_mounts.SYSTEM_SLUG):
            try:
                membership_mod.require_role(wsr.root, slug, subject, "contributor")
            except MembershipError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from None
        target = _read_target(request, slug, write=True)
        try:
            base, rel = wpaths.locate_inside(target, p["path"])
            wpaths.write_text_inside(base, rel, body)
        except wpaths.PathRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if not (target / ".git").is_dir():
            return None
        email = request.headers.get("x-user-email", "") or f"{subject}@vexa.local"
        run_git(target, "add", "--", p["path"])
        run_git(target, "-c", f"user.name={subject}", "-c", f"user.email={email}", "commit", "-m",
                f"accept: {p['title'] or p['path']} (proposed after a call)", "--", p["path"])
        return run_git(target, "rev-parse", "HEAD").stdout.strip() or None

    # ── WHAT THE ROOM'S AGENT CHANGED DURING A CALL, AND ITS UNDO (R1 1.5) ───────────────────────

    def _call_of(request: Request, room: str) -> tuple[str, dict, dict]:
        """``(subject, call, session row)`` for a call the caller is in — else 404, like a session."""
        from control_plane import rooms  # noqa: PLC0415
        if not rooms.enabled(_global_root, str(subject_of(request))):
            from control_plane import features  # noqa: PLC0415
            raise HTTPException(status_code=403, detail=features.refusal("rooms"))
        s = _store()
        call = s.call_by_room(room)
        subject = str(subject_of(request))
        if call is None or not s.can_read(call["session_id"], subject):
            raise HTTPException(status_code=404, detail="call not found")
        return subject, call, s.get(call["session_id"])

    def _change_view(c: dict) -> dict:
        return {k: c[k] for k in ("id", "workspace", "path", "at", "on_behalf_of", "undone_by", "undone_at")} | {
            "created": c["base"] is None, "undoable": c["undone_at"] is None and c["base"] is not None}

    @router.get("/api/calls/{room}/changes")
    def call_changes(room: Annotated[str, Path(pattern=ROOM_PATTERN)], request: Request):
        """What the room's agent changed in its workspace during this call, oldest first."""
        _subject, call, _row_ = _call_of(request, room)
        return {"room": room, "session_id": call["session_id"], "meeting_id": call.get("meeting_id") or None,
                "changes": [_change_view(c) for c in _store().call_changes(room)]}

    @router.post("/api/calls/{room}/changes/{change_id}/undo")
    def call_change_undo(room: Annotated[str, Path(pattern=ROOM_PATTERN)],
                         change_id: Annotated[str, Path(pattern=r"^cc-[0-9a-f]{16}$")], request: Request):
        """UNDO ONE CHANGE: the page goes back to what it was before the agent wrote it, through the
        page write path, committed in the caller's name. Refused when the page changed since (someone
        wrote over it) — a proposal to restore it is offered instead, for its owner to decide."""
        refuse_delegated(request, reason="undo_is_a_person",
                         instruction="Undoing a change is the person's act. Suggest it and stop.")
        subject, call, row = _call_of(request, room)
        s = _store()
        c = s.call_change(change_id)
        if c is None or c["room"] != room:
            raise HTTPException(status_code=404, detail="change not found")
        if not s.can_write(call["session_id"], subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        if c["undone_at"] is not None:
            raise HTTPException(status_code=409, detail={"refused": "already_undone"})
        require_in_ceiling(request, c["workspace"])
        try:
            membership_mod.require_role(wsr.root, c["workspace"], subject, "contributor")
        except MembershipError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        target = _read_target(request, c["workspace"], write=True)
        import hashlib  # noqa: PLC0415
        from control_plane import page_merge  # noqa: PLC0415
        with page_merge.write_lock(target):
            now = wpaths.read_text_inside(target, c["path"])
            if now is None or hashlib.sha256(now.encode("utf-8")).hexdigest() != c["after_digest"]:
                proposal = None
                if c["base"] is not None:
                    proposal = s.propose(call["session_id"], owner=subject, kind="page", body=c["base"],
                                         title=f"Restore {c['path']} as it was before the call's change",
                                         workspace=c["workspace"], path=c["path"],
                                         meeting_id=call.get("meeting_id") or None)
                raise HTTPException(status_code=409, detail={
                    "refused": "changed_since",
                    "said": "The page changed after Vexa's edit, so it was not undone. A proposal to "
                            "restore it was made instead." if proposal else
                            "The page changed after Vexa's edit, so it was not undone.",
                    **({"proposal": proposal["id"]} if proposal else {})})
            if c["base"] is None:
                raise HTTPException(status_code=409, detail={
                    "refused": "not_undoable", "said": "This change made a new page; remove it instead."})
            try:
                fbase, frel = wpaths.locate_inside(target, c["path"])
                wpaths.write_text_inside(fbase, frel, c["base"])
            except wpaths.PathRefused as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            commit = None
            if (target / ".git").is_dir():
                email = request.headers.get("x-user-email", "") or f"{subject}@vexa.local"
                run_git(target, "add", "--", c["path"])
                run_git(target, "-c", f"user.name={subject}", "-c", f"user.email={email}", "commit", "-m",
                        f"undo: {c['path']} (Vexa's change during the call)", "--", c["path"])
                commit = run_git(target, "rev-parse", "HEAD").stdout.strip() or None
        s.undo_call_change(change_id, subject)
        try:
            s.add_note(call["session_id"], "vexa", f"Undid Vexa's change to {c['path']}.")
        except Exception:  # noqa: BLE001
            pass
        return {"undone": True, "commit": commit, "change": _change_view(s.call_change(change_id))}

    def _decide(session: str, proposal_id: str, request: Request, *, accept: bool,
                body: Optional[str]) -> dict:
        subject, row = _row(request, session)
        s = _store()
        p = s.proposal(proposal_id)
        if p is None or p["session_id"] != row["id"]:
            raise HTTPException(status_code=404, detail="proposal not found")
        if str(subject) != p["owner"]:
            raise HTTPException(status_code=403, detail="not_meeting_owner")
        if p["status"] != "open":
            raise HTTPException(status_code=409, detail="already_decided")
        commit = _land(request, str(subject), p, body if body is not None else p["body"]) if accept else None
        try:
            done = s.decide(proposal_id, str(subject), accept=accept, body=body)
        except SessionDenied as exc:
            raise HTTPException(status_code=409, detail=exc.reason) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"proposal": done, "commit": commit}

    @router.post("/api/sessions/{session}/proposals/{proposal_id}/accept")
    def accept_change(session: SessionPath, proposal_id: Annotated[str, Path(pattern=r"^p-[0-9a-f]{16}$")],
                        body: DecideBody, request: Request):
        """The meeting's owner accepts a proposal, optionally edited. A page or entity is written to its
        workspace and committed with the owner as author; a commitment or note is recorded accepted."""
        return _decide(session, proposal_id, request, accept=True, body=body.body)

    @router.post("/api/sessions/{session}/proposals/{proposal_id}/reject")
    def reject_change(session: SessionPath, proposal_id: Annotated[str, Path(pattern=r"^p-[0-9a-f]{16}$")],
                        request: Request):
        return _decide(session, proposal_id, request, accept=False, body=None)

    return router
