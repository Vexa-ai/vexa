"""routers/sessions.py — a session's people (ADR-0048): who is in a conversation, adding and removing
them, and the messages posted to it that the agent was not asked to answer.

A session is addressed the way the chat addresses it: the caller's own thread by its name, or a
session they were added to by its store id (``s-…``). Every route answers ``503`` when the session
store is not configured, and ``404`` for a session the caller is not in — the same answer as one
that does not exist.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal, Optional

from control_plane.ceiling import is_delegated
from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import people_directory, person_resolution, room_show, rooms, session_fork, session_sync
from control_plane.bodies import CHAT_SESSION_PATTERN
from control_plane.session_store import SessionDenied

logger = logging.getLogger("agent_api.sessions")

NOT_CONFIGURED = "Shared sessions are not configured on this deployment"
_PRINCIPAL = r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,199}$"


class ParticipantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Optional[str] = Field(default=None, max_length=320, description="a person, by email")
    subject: Optional[str] = Field(default=None, pattern=_PRINCIPAL, description="a person, by id")
    agent: Optional[str] = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$",
                                 description="an agent, by name")
    role: Literal["member", "viewer"] = "member"
    excerpt: Optional[list[int]] = Field(
        default=None, max_length=session_fork.EXCERPT_MAX_TURNS,
        description="when a private chat becomes shared: the turns (by their index in its history) "
                    "whose visible text the new shared chat starts with")
    history: Optional[str] = Field(
        default=None, pattern=r"^(all|none|from:[0-9]{1,4})$",
        description="the OWNER's choice when a private chat becomes shared: `all` (the default) shares "
                    "this chat in place, history included; `from:<turn>` starts a new shared chat "
                    "carrying the visible text from that turn (its index in the history) on; `none` "
                    "starts a new empty one. Before sharing a chat with history, say in one line what "
                    "in it is sensitive (figures, mail, internal documents) and proceed on the owner's "
                    "yes. Nobody but the owner chooses; anyone else adding a person starts a new empty "
                    "chat")
    title: Optional[str] = Field(default=None, max_length=200, description="the shared chat's title")
    pressed: Optional[str] = Field(
        default=None, max_length=128, pattern=r"^[^\s\]]{1,128}$",
        description="the `#<pending>` of the person's pressed answer to the card that offered this add "
                    "(`[answer <card> #<pending>] …`). An agent adds a person only on that press")


def build(**d) -> APIRouter:
    router = APIRouter()
    store = d.get("session_store")
    subject_of = d["subject_of"]
    lookup = d.get("_email_subject_lookup")
    # D35: one Person, many addresses — the picker lists Persons, a grant reaches every linked account
    persons = d.get("person_resolver") or person_resolution.default()
    wsr = d.get("wsr")
    mindex = d.get("mindex")
    notifier = d.get("notifier")
    sess = d.get("sess")
    settings = d.get("settings")
    workspace_registry = d.get("workspace_registry")
    redis_url = d.get("redis_url")

    def _index_list(who: str) -> list:
        if mindex is None:
            return []
        return list(mindex.list(who) or [])

    def _mentionable(subject: str) -> list[dict]:
        if wsr is None:
            return []
        return people_directory.mentionable(wsr.root, subject, _index_list)

    def _press_for(request: Request, row: dict, subject: str, body, principal: str, *, fresh: bool) -> None:
        """AN AGENT ADDS A PERSON ONLY ON THE PERSON'S PRESS (D13, 7g). A delegated caller names the
        press (``pressed``); it must be the speaker's own answered card in THIS chat, naming the person
        added, pressed on its primary button — or its secondary when the add starts a fresh chat or
        room (``fresh``) — and it is spent here, so one press is one add. The person's own app adds
        without one: the press is theirs to make."""
        if not is_delegated(request):
            return
        from control_plane.routers import asks  # noqa: PLC0415
        names = asks.aliases(_mentionable(subject), principal, (body.email or "").strip().lower(), body.subject or "")
        asks.require_press(_store(), request, body.pressed, subject=str(subject), targets=[names],
                           act="Adding a person", session_id=row["id"],
                           choices=("primary", "secondary") if fresh else ("primary",))

    def _mentionable_view(subject: str) -> list[dict]:
        """THE PICKER'S ANSWER (security review M1, deny test 16, 0.13.4 S4): an undiscoverable member
        is absent, and a co-member's address is shown only where the caller writes (owner or
        contributor) in a workspace they share — a reader gets names only."""
        out = []
        for p in _mentionable(subject):
            if p.get("discoverable") is False:
                continue
            row = {k: v for k, v in p.items() if k != "discoverable"}
            if p.get("email") and not people_directory.sees_addresses(wsr.root, subject, p.get("workspaces") or []):
                row["email"] = ""
            out.append(row)
        return out

    def _store():
        if store is None:
            raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
        return store

    def _row(request: Request, session: str, *, create: bool = False) -> tuple[str, dict]:
        s = _store()
        subject = subject_of(request)
        row = session_sync.resolve(s, subject, session, create=create)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        return subject, row

    def _actor_name(request: Request, subject: str) -> str:
        if wsr is None:
            return (request.headers.get("x-user-email") or "").split("@", 1)[0]
        return people_directory.name_of(wsr.root, subject, _index_list,
                                        request.headers.get("x-user-email") or "")

    def _notify_added(request: Request, subject: str, target: dict, principal: str, *, forked: bool,
                      title: str = "") -> None:
        """``chat_shared`` (a private chat became a new shared one) or ``chat_added`` (an existing
        shared chat), to the person added — and their rail is told to read again, so the chat is
        there without a reload. Emitted by this route only (#1783)."""
        if notifier is None:
            return
        try:
            notifier.emit([principal], kind="chat_shared" if forked else "chat_added", actor=subject,
                          actor_name=_actor_name(request, subject), object_kind="chat",
                          object_id=target["id"], object_name=target.get("title") or title or "a chat")
            notifier.rail([principal, subject], session_id=target["id"], change="added")
        except Exception as exc:  # noqa: BLE001 — the add happened; its notice is the extra
            logger.warning("chat-add notification not sent (%s)", type(exc).__name__)

    def _names(parts: list) -> dict:
        """How each person in the chat is named to the others (their roster name or address), so the
        chat says "Collab b", never an account id. A person nobody can name is left out."""
        out = {}
        for p in parts:
            if p.get("kind") != "human":
                continue
            try:
                n = people_directory.name_of(wsr.root, p["principal"], _index_list) if wsr is not None else ""
            except Exception:  # noqa: BLE001 — a name is a courtesy
                n = ""
            if n:
                out[p["principal"]] = n
        return out

    def _words(m: dict) -> dict:
        """A line as the person wrote it: anything up to the terminal's context sentinel (lines stored
        before the chat route cut it) is the agent's grounding, never shown to the others."""
        t = str(m.get("text") or "")
        i = t.rfind("<!--vexa:user-input-below-->")
        return {**m, "text": t[i + len("<!--vexa:user-input-below-->"):].strip()} if i >= 0 else m

    def _view(row: dict, subject: str) -> dict:
        s = _store()
        parts = s.participants(row["id"])
        return {"session_id": row["id"], "owner": row["owner"], "you": subject,
                "role": s.role_of(row["id"], subject),
                "participants": parts, "names": _names(parts)}

    @router.get("/api/people/mentionable")
    def mentionable(request: Request):
        """The people the caller may @-mention, and so add to a chat: every member of every group
        workspace the caller is a member of — nobody else — and the agents a chat may address.
        Each person carries their stable id (``subject``), so a mention names exactly one account."""
        subject = subject_of(request)
        return {"people": person_resolution.group_people(_mentionable_view(subject), persons),
                "agents": [{"name": a} for a in people_directory.AGENTS]}

    @router.get("/api/sessions/{session}/share-preview")
    def share_preview(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request):
        """What adding a person here would do, for the confirmation the owner reads before it
        happens: a private chat starts a new shared chat (``forks``); a routine's chat says which of
        the owner's private reads its runs would lose once others read them (``private_reads``)."""
        subject, row = _row(request, session, create=True)
        if _store().role_of(row["id"], subject) != "owner":
            raise HTTPException(status_code=403, detail="not_session_owner")
        return session_fork.share_preview(_store(), row)

    @router.get("/api/sessions/{session}/participants")
    def list_participants(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request):
        """Who is in this conversation, with their roles. Any participant may read it."""
        subject, row = _row(request, session)
        return _view(row, subject)

    def _old_layout(subject: str, name: str):
        """The owner's thread as the workspace layout holds it (a thread the store has no version of)."""
        def read() -> list:
            if wsr is None or name.startswith(session_sync.STORE_ID_PREFIX):
                return []
            try:
                return wsr.history(subject, name)
            except Exception:  # noqa: BLE001 — unreadable: an excerpt then names a missing turn
                return []
        return read

    def _invite_to_room(request: Request, session: str, body) -> dict:
        s = _store()
        subject, row = _row(request, session, create=True)
        room = s.room(row["id"])
        if room is None:
            # A ROOM IS JUST A CHAT WITH MORE THAN ONE PERSON (founder, 2026-10-16): the owner adding
            # someone to a private chat that writes to a workspace turns it into a room of that
            # workspace — history from the start visible to them — or, with `history: none`, starts
            # a fresh room there with them and leaves this chat private.
            target = ""
            try:
                target = (sess.target(subject, row.get("name") or row["id"]) or "").strip() if sess is not None else ""
            except Exception:  # noqa: BLE001
                target = ""
            if s.role_of(row["id"], subject) != "owner" or not target or target in (str(subject), "desk"):
                raise HTTPException(status_code=409, detail={
                    "refused": "chats_are_private",
                    "note": "this chat writes to no workspace; choose the workspace it writes to, then add the person"})
            room = {"workspace": target, "_new": True}
        if not s.can_write(row["id"], subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        if body.agent:
            raise HTTPException(status_code=422, detail="a room's members are people")
        principal = body.subject or ""
        if body.email:
            resolved = lookup(body.email.strip().lower()) if lookup is not None else None
            principal = str(resolved) if resolved else ""
        ws = room.get("workspace") or ""
        if (not principal and not (body.email and ws)) or principal == subject:
            return _view(row, subject)            # an unknown id is answered like a known one
        if ws and wsr is not None:
            from control_plane import workspace_membership  # noqa: PLC0415
            try:
                member = bool(principal) and workspace_membership.is_member(wsr.root, ws, principal) is not None
            except Exception:  # noqa: BLE001
                member = False
            # AN ADDRESS WITH NO ACCOUNT IS ANSWERED LIKE AN ACCOUNT OUTSIDE THE WORKSPACE: neither
            # is in it, and the same refusal says nothing about whether the address signs in here.
            if not member:
                title = ws
                try:
                    rec = workspace_registry.by_slug(ws) if workspace_registry is not None else None
                    title = (rec or {}).get("name") or ws
                except Exception:  # noqa: BLE001
                    pass
                raise HTTPException(status_code=403, detail={
                    "refused": "not_a_workspace_member", "workspace": ws, "title": title,
                    "said": (f"They are not in {title}. Invite them to {title} (workspace_invite) on the "
                             "same card, then add them here.")})
        if not principal:
            return _view(row, subject)
        # WHO IS IN A ROOM IS THE ROOM-MEMBERSHIP AUTHORITY'S (0.13.4 D36): a closed room's rows are
        # written to the registry first (the maker's own seeded), the participant row is the projection
        from control_plane import pair_rooms, room_membership  # noqa: PLC0415
        if ws and wsr is not None and pair_rooms.is_pair(wsr.root, ws):
            raise HTTPException(status_code=409, detail={
                "refused": "room_of_two", "said": "A room of two has exactly two people. Start a room in a "
                                                  "workspace to talk with more."})
        _press_for(request, row, subject, body, principal,
                   fresh=bool(room.get("_new")) and body.history == "none")
        if room.get("_new"):
            if body.history == "none":
                fresh = s.create(subject, name=None, title=row.get("title") or "Room")
                s.put_room(fresh["id"], workspace=ws, visibility="closed")
                room_membership.seed(s, wsr.root, fresh["id"])
                room_membership.add(s, wsr.root, fresh["id"], principal, actor=str(subject))
                _notify_added(request, subject, fresh, principal, forked=True, title=fresh.get("title") or "")
                return _view(fresh, subject)
            s.put_room(row["id"], workspace=ws, visibility="closed")
            room_membership.seed(s, wsr.root, row["id"])
        if s.role_of(row["id"], principal) is None:
            room_membership.add(s, wsr.root, row["id"], principal, actor=str(subject))
            _notify_added(request, subject, row, principal, forked=False, title=row.get("title") or "")
        return _view(row, subject)

    @router.post("/api/sessions/{session}/participants")
    def add_participant(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)],
                        body: ParticipantBody, request: Request):
        """Add a person (by email or id) or an agent to the conversation. Adding someone gives them
        this conversation and nothing else: no workspace is shared, and from now on a turn here uses
        only what every participant can read (ADR-0048 decision 4).

        The OWNER of a chat only they are in decides what of it the person sees (``history``):
        ``all`` — they join this chat, history included; ``from:<turn>`` — a new shared chat with the
        visible text from that turn on; ``none`` — a new empty shared chat. Named nothing, it is
        ``all``: "share this chat with her" means this chat (the agent names what is sensitive in
        one line before it calls this, and proceeds on the owner's yes). A member who is not the
        owner adding a person starts a new empty chat of their own with them: the owner's history
        is the owner's to disclose. An address with no account is answered like one with one."""
        named = [v for v in (body.email, body.subject, body.agent) if v]
        if len(named) != 1:
            raise HTTPException(status_code=422, detail="name exactly one of email, subject or agent")
        if rooms.enabled(d.get("_global_root"), str(subject_of(request))):
            # ROOMS ON (founder 2026-10-15): a chat is always private — nobody is added to one; a
            # room's member invites a member of the room's workspace into it.
            return _invite_to_room(request, session, body)
        if body.excerpt is not None and body.history is not None:
            raise HTTPException(status_code=422, detail="name either history or excerpt")
        s = _store()
        subject, row = _row(request, session, create=True)
        role = s.role_of(row["id"], subject)
        owner = role == "owner"
        if not owner and (role != "member" or body.agent):
            raise HTTPException(status_code=403, detail="not_session_owner")
        if not owner and (body.history not in (None, "none") or body.excerpt is not None):
            raise HTTPException(status_code=403, detail="history_is_the_owners")
        kind, principal = "human", body.subject or ""
        if body.agent:
            kind, principal = "agent", body.agent
        elif body.email:
            resolved = lookup(body.email.strip().lower()) if lookup is not None else None
            principal = str(resolved) if resolved else ""
        private = session_fork.needs_fork(s, row, kind=kind)
        history = body.history
        if owner and history is None and body.excerpt is None:
            history = "all"
        if owner and not private and (body.excerpt is not None or history not in (None, "all")):
            raise HTTPException(status_code=422,
                                detail="this chat is already shared: a person added joins it as it is")
        fork = (not owner) or (private and history != "all")
        carried = None
        if fork and owner and (body.excerpt or (history or "").startswith("from:")):
            try:
                turns = session_fork.private_history(s, row, _old_layout(subject, row["name"]))
                picks = (body.excerpt if body.excerpt
                         else session_fork.turns_from(turns, int(history.split(":", 1)[1])))
                carried = session_fork.excerpt(turns, picks)
            except session_fork.ExcerptError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
        if kind == "human" and principal and not any(
                p["subject"] == principal for p in _mentionable(subject)):
            # A PERSON, BY ID OR BY ADDRESS, is someone the owner shares a workspace with (the
            # @-mention set; an address resolves to its account first), and the answer for anyone
            # else is the one an unknown address gets: nothing is created.
            return _view(row, subject)
        if not principal:
            # no account with that address: nothing is created, and the answer is the one a known
            # address gets — the chat's people, as they now are
            return _view(row, subject)
        if principal == subject:
            raise HTTPException(status_code=403, detail="owner_role_fixed")
        _press_for(request, row, subject, body, principal, fresh=fork)
        target = row
        if fork:
            target = session_fork.fork(s, row, subject, title=(body.title or "").strip(),
                                       carried=carried)
        already = s.role_of(target["id"], principal) is not None
        try:
            s.add_participant(target["id"], subject, principal, kind=kind, role=body.role)
        except SessionDenied as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if kind == "human" and not already:
            # a private chat shared — in place or as a new chat — is "shared with you"
            _notify_added(request, subject, target, principal, forked=bool(fork) or (private and kind == "human"),
                          title=row.get("title") or "")
            # …AND IT WRITES, FOR THEM, WHERE IT WRITES FOR ITS OWNER (Vexa-ai/vexa#1783): the
            # "shared work" profile bound to that workspace. A chat on the owner's desk binds nothing.
            if sess is not None and wsr is not None and mindex is not None:
                try:
                    from control_plane.routers.workspace_states import bind_shared_work_for  # noqa: PLC0415
                    wid = sess.target(subject, session)
                    if wid:
                        bind_shared_work_for(sess, settings, wsr.root, mindex, workspace_registry,
                                             principal, target["id"], wid)
                except Exception:  # noqa: BLE001 — the share happened; the binding is the extra
                    logger.warning("shared work not bound on share")
        if kind == "human":
            # D35: the person picked is a Person — every other account THEY CONFIRMED as theirs joins
            # too; an unconfirmed link gets nothing. Fails toward fewer: no CRM answer, no extra account.
            for other in person_resolution.linked_accounts(principal, resolver=persons)[1:]:
                if other == subject or s.role_of(target["id"], other) is not None:
                    continue
                try:
                    s.add_participant(target["id"], subject, other, kind=kind, role=body.role)
                except (SessionDenied, ValueError):
                    continue
                _notify_added(request, subject, target, other, forked=bool(fork) or private,
                              title=row.get("title") or "")
        return _view(target, subject)

    @router.delete("/api/sessions/{session}/participants/{principal}")
    def remove_participant(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)],
                           principal: Annotated[str, Path(pattern=_PRINCIPAL)], request: Request):
        """Remove a participant (the owner), or leave (anyone, naming themself). The owner stays."""
        subject, row = _row(request, session)
        try:
            removed = _store().remove_participant(row["id"], subject, principal)
        except SessionDenied as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from None
        if removed:
            # LEAVING OR BEING REMOVED TAKES THE CHAT OFF THAT PERSON'S RAIL (#1783): their filing of it
            # is dropped, and every tab they have open is told to read the rail again.
            try:
                from control_plane import chat_folders  # noqa: PLC0415

                chat_folders.forget(_store(), principal, f"c:{row['id']}")
            except Exception as exc:  # noqa: BLE001 — an orphan filing names a chat nobody lists
                logger.warning("filing not dropped on leave (%s)", type(exc).__name__)
            if notifier is not None:
                notifier.rail([principal, subject], session_id=row["id"], change="removed")
        return {"removed": removed, "session_id": row["id"],
                "participants": _store().participants(row["id"])}

    @router.get("/api/sessions/{session}/messages")
    def list_messages(session: Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)], request: Request):
        """Messages posted here that the agent was not asked to answer, oldest first."""
        subject, row = _row(request, session)
        s = _store()
        # LIVE IN A SHARED CHAT (#1783, collaboration rehearsal): the chat reads this every few
        # seconds while open. `version` moves when anyone's turn lands, so the other person's tab
        # re-reads the conversation; `names` says who wrote each line.
        fresh = s.get(row["id"]) or row
        # READ BY THE PERSON (#1783 R3): their own app reading the room is them reading it, so its
        # unread count clears. An agent reading on someone's behalf (a delegated call) never does.
        if not is_delegated(request):
            try:
                s.mark_read(row["id"], subject)
            except Exception:  # noqa: BLE001 — a read mark that cannot be kept costs the badge only
                pass
        return {"session_id": row["id"], "you": subject, "version": int(fresh.get("version") or 0),
                "messages": [_words(m) for m in s.notes(row["id"])],
                "names": _names(s.participants(row["id"])),
                # what a member is showing the room, if anything: every member's app opens it
                "showing": room_show.current(row["id"]),
                **_turn_status(row["id"])}

    def _turn_status(session_id: str) -> dict:
        """A ROOM'S LIVE STATUS above the composer (founder, 2026-10-16): "Vexa heard you · thinking…"
        from the moment a wake is accepted, the step the turn is on, "nothing to add" briefly."""
        from control_plane import rooms as _rooms, session_sync as _ss  # noqa: PLC0415
        try:
            if not _rooms.is_room(_store(), session_id):
                return {}
            r = _rooms.redis_for(redis_url)
            held = bool(r is not None and r.get(_ss.lease_key(session_id)))
            turn = _rooms.turn_status(r, session_id, busy=held)
            return {"agent_turn": turn, "agent_step": _rooms.current_step(r, session_id) if turn == "thinking" else None}
        except Exception:  # noqa: BLE001 — a status that cannot be read is no status
            return {}

    return router
