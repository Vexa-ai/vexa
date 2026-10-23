"""routers/room_people.py — WHO IS IN A ROOM, AND HOW SOMEONE GETS IN (PRD 0.13.4 S4: D21, D36, FD4).

    POST   /api/rooms/{session}/tag                   what @mentioning this person would do (the tag card)
    POST   /api/rooms/{session}/members               the card's answer: add (and tell), or invite
    DELETE /api/rooms/{session}/members/{principal}   a closed room's owner removes someone (and Undo)
    GET    /api/rooms/{session}/people                the members panel: members, invited, the rest
    POST   /api/rooms/direct                          "Message <person>": the room of two, made once
    GET    /api/rooms/{session}/switches              the two switches, their locks and asks
    POST   /api/rooms/{session}/switches              turn one on or off (or ask, in a room of two)
    POST   /api/rooms/{session}/switches/{switch}/answer   the other person's consent card

A MENTION NEVER SILENTLY CHANGES WHO HAS ACCESS (R9). Tagging someone posts the message; whether
anything else happens is a card only the tagger sees (sharing-tagging §1), FOUR CASES:

1. a ROOM MEMBER — only mentioned (notified through their own gate); no card;
2. a WORKSPACE MEMBER not in the room — the three-way card: *Add and tell* · *Yes, and always for this
   workspace* (a workspace policy line, proposed per I15 — applied at once only when the tagger is
   the workspace's owner in a browser session) · *Just mention*;
3. a KNOWN PERSON without an account (or with one, outside the workspace) — an invite card: a workspace
   invite bound to their address, the role capped at the tagger's, and the room as pending membership;
4. an UNKNOWN ADDRESS — decided by the workspace's guest-vs-invite line (decision 9). The floor refuses
   guest seats until audience-limited mounts exist, so the seeded default, invite, is what happens.

Cases 3 and 4 answer the SAME way whether the address has an account or not (security review M1:
no oracle). An AGENT-COMPOSED mention (``origin: agent``, or any delegated caller) never triggers the
"always" line (deny test 29) and never adds anyone; every act here is a person's click.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import membership_acts, pair_rooms, people_directory, policy_decide as pd
from control_plane import policy_proposals as pp
from control_plane import publish as publish_mod
from control_plane import room_membership as rm
from control_plane import room_ring, room_switches, rooms, session_sync
from control_plane import workspace_membership as m
from control_plane.bodies import CHAT_SESSION_PATTERN
from control_plane.ceiling import is_delegated, refuse_delegated, require_session_credential

logger = logging.getLogger("agent_api.room_people")

_PRINCIPAL = r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,199}$"
SessionPath = Annotated[str, Path(pattern=CHAT_SESSION_PATTERN)]
MENTION_ADDS = "room.mention_adds_member"


class PersonRef(BaseModel):
    """Who is tagged: an account id the picker offered, or an address; a known person's name."""
    model_config = ConfigDict(extra="forbid")
    subject: Optional[str] = Field(default=None, pattern=_PRINCIPAL, description="an account id from the picker")
    email: Optional[str] = Field(default=None, max_length=320, description="an address (typed, or a contact's)")
    name: Optional[str] = Field(default=None, max_length=120, description="how the picker names them")
    known: bool = Field(default=False, description="the picker knows them (a CRM person or a contact)")
    origin: Literal["person", "agent"] = Field(default="person",
                                               description="who composed the mention: the person typing, or an agent")


class AddBody(PersonRef):
    tell: bool = Field(default=True, description="tell them (through their own notification gate)")
    always: bool = Field(default=False, description="and always for this workspace (case 2 only)")


class DirectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(pattern=_PRINCIPAL, description="the other person's account id")


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    switch: Literal["vexa", "transcribe"]
    on: bool


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept: bool


def build(**d) -> APIRouter:
    router = APIRouter()
    store = d.get("session_store")
    subject_of = d["subject_of"]
    wsr = d.get("wsr")
    mindex = d.get("mindex")
    notifier = d.get("notifier")
    settings = d.get("settings")
    lookup = d.get("_email_subject_lookup")
    workspace_registry = d.get("workspace_registry")
    livekit = d.get("livekit")
    _pc = d.get("_pc")
    _global_root = d.get("_global_root")
    _internal_caller = d.get("_internal_caller")
    sess = d.get("sess")

    def _store():
        if store is None:
            raise HTTPException(status_code=503, detail={"refused": "session_store_off"})
        return store

    def _root():
        if wsr is None:
            raise HTTPException(status_code=503, detail={"refused": "workspaces_off"})
        return wsr.root

    def _index_list(who: str) -> list:
        return list(mindex.list(who) or []) if mindex is not None else []

    def _name(who: str, email: str = "") -> str:
        try:
            return people_directory.name_of(_root(), str(who), _index_list, email) or str(who)
        except Exception:  # noqa: BLE001 — a name is a courtesy
            return str(who)

    def _ws_name(ws: str) -> str:
        try:
            rec = workspace_registry.by_slug(ws) if workspace_registry is not None else None
            return str((rec or {}).get("name") or ws)
        except Exception:  # noqa: BLE001
            return ws

    def _room_row(request: Request, session: str) -> tuple[str, dict, dict]:
        subject = str(subject_of(request))
        row = session_sync.resolve(_store(), subject, session, create=False)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        room = _store().room(row["id"])
        if room is None:
            raise HTTPException(status_code=409, detail={"refused": "not_a_room",
                                                         "said": "This is a private chat, not a room."})
        return subject, row, room

    def _agent_composed(request: Request, body: PersonRef) -> bool:
        return body.origin == "agent" or is_delegated(request)

    def _live_call(session_id: str) -> Optional[dict]:
        try:
            return _store().active_call(session_id) if livekit is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _say(session_id: str, text: str) -> None:
        try:
            _store().add_note(session_id, rooms.AGENT_AUTHOR, text)
        except Exception as exc:  # noqa: BLE001 — the act happened; its line is the extra
            logger.warning("room line not posted (%s)", type(exc).__name__)

    # ── who is tagged, and what tagging them does ───────────────────────────────────────────────

    def _resolve(subject: str, body: PersonRef) -> tuple[str, str]:
        """``(account id or "", address or "")``. An account id is honoured only when it is someone
        the tagger shares a workspace with (the picker's set); anything else is treated like an
        address nobody knows — the same answer either way (M1)."""
        email = str(body.email or "").strip().lower()
        who = ""
        if body.subject:
            try:
                if people_directory.may_mention(_root(), subject, body.subject, _index_list):
                    who = str(body.subject)
            except Exception:  # noqa: BLE001 — an unreadable directory names nobody
                who = ""
        elif email and lookup is not None:
            try:
                who = str(lookup(email) or "")
            except Exception:  # noqa: BLE001
                who = ""
        return who, email

    def _rule_allows_add(request: Request, subject: str, ws: str, body: PersonRef) -> Optional[str]:
        """The "always" line for this tagger in this workspace, or None. Never for an agent's mention."""
        if _agent_composed(request, body):
            return None
        out = pd.decide(pd.Ask(principal=subject, action=MENTION_ADDS, workspace=ws,
                               credential=pd.credential_of(request.headers)), root=_root())
        return out.rule if out.allowed else None

    def _classify(request: Request, subject: str, row: dict, room: dict, body: PersonRef) -> dict:
        s, root = _store(), _root()
        ws = room.get("workspace") or ""
        who, email = _resolve(subject, body)
        pair = pair_rooms.is_pair_room(s, root, row["id"])
        if who and s.role_of(row["id"], who) is not None:
            return {"case": "member", "subject": who, "name": _name(who)}
        if pair:
            # a room of two has two people: anyone else is "start a room in a workspace"
            return {"case": "pair_full", "said": "A room of two has exactly two people. Start a room in "
                                                 "a workspace to talk with more."}
        in_ws = bool(who) and m.is_member(root, ws, who) is not None
        if in_ws:
            return {"case": "workspace_member", "subject": who, "name": _name(who),
                    "workspace": ws, "workspace_name": _ws_name(ws),
                    "always_offered": not _agent_composed(request, body),
                    "visibility": room.get("visibility")}
        typed = bool(email)
        if not email and who:
            # someone the tagger knows from another workspace: the invite goes to their roster address,
            # which the tagger is never shown (M1) — the card names them and masks the address
            try:
                email = next((str(p.get("email") or "") for p in people_directory.mentionable(root, subject, _index_list)
                              if p["subject"] == who), "")
            except Exception:  # noqa: BLE001
                email = ""
        if not email:
            return {"case": "unknown", "said": "Name an address to invite them."}
        # "known" is what the PICKER knows (a contact, or someone from another shared workspace) —
        # never whether a typed address has an account here (M1: no oracle)
        known = bool(body.known or (who and body.subject))
        guest = pd.decide(pd.Ask(principal=subject, action="room.guest_seat", workspace=ws,
                                 credential=pd.credential_of(request.headers)), root=root)
        shown = email if typed else "…@" + email.split("@", 1)[-1]
        return {"case": "known" if known else "unknown_email", "email": shown, "_address": email,
                "name": (body.name or "").strip() or (_name(who) if (who and body.subject) else
                                                       (email if not known else email.split("@", 1)[0])),
                "workspace": ws, "workspace_name": _ws_name(ws),
                "seat": "guest" if guest.allowed else "invite", "seat_rule": guest.rule,
                "role": _invite_role(root, ws, subject)}

    def _invite_role(root, ws: str, tagger: str) -> str:
        """Contributor, capped at the tagger's own role (PRD §10 Q14)."""
        try:
            mine = m.normalize_role(m.is_member(root, ws, tagger) or "viewer")
        except Exception:  # noqa: BLE001
            mine = "viewer"
        return "contributor" if mine in ("owner", "contributor") else "reader"

    def _card(c: dict, row: dict, room: dict, live: bool) -> Optional[dict]:
        """The card the tagger sees, as data the client renders (7g). None for a plain mention."""
        title = row.get("title") or "this room"
        ws_name = c.get("workspace_name") or ""
        if c["case"] == "workspace_member":
            gets = (f"#{title} and its history" if room.get("visibility") == "open"
                    else f"#{title} and its history (a closed room)")
            return {"kind": "invitation", "case": c["case"], "only_you": True,
                    "title": f"Add {c['name']} to #{title} and tell them?",
                    "who": f"{c['name']} · in {ws_name}, not in #{title}",
                    "gets": gets + (" · rung into the live call" if live else ""),
                    "actions": ["add", *(["always"] if c.get("always_offered") else []), "mention"],
                    "always_line": (f"In {ws_name}, when I @mention a workspace member who isn't in the room, "
                                    "add them and tell them.")}
        if c["case"] in ("known", "unknown_email"):
            return {"kind": "invitation", "case": c["case"], "only_you": True,
                    "title": f"Invite {c['name']} to {ws_name} and #{title}, and tell them?",
                    "who": c["email"],
                    "gets": f"{ws_name} as a {c['role']}, and #{title} (an invite bound to this address, 14 days)"
                            + (" · the message says a call is on" if live else ""),
                    "actions": ["invite", "mention"],
                    "never_always": "No “always” here: bringing someone new into a workspace is decided "
                                    "person by person."}
        return None

    @router.post("/api/rooms/{session}/tag")
    def room_tag(session: SessionPath, request: Request, body: PersonRef = Body(...)):
        """WHAT @MENTIONING THIS PERSON DOES here: ``case`` (member · workspace_member · known ·
        unknown_email · pair_full · unknown) and the card only the tagger sees. With the tagger's own
        "always" line in force (and a person's mention), a workspace member is added at once and the
        answer says so (``added_by_rule``, with Undo). Nothing else changes anything."""
        if not (body.subject or body.email):
            raise HTTPException(status_code=422, detail="name a subject or an email")
        subject, row, room = _room_row(request, session)
        c = _classify(request, subject, row, room, body)
        live = _live_call(row["id"]) is not None
        if c["case"] == "workspace_member":
            rule = _rule_allows_add(request, subject, room.get("workspace") or "", body)
            if rule and _store().can_write(row["id"], subject):
                done = _add_member(request, subject, row, room, c["subject"], tell=True)
                return {**c, "case": "added_by_rule", "rule": rule, **done,
                        "undo": {"method": "DELETE", "path": f"/api/rooms/{row['id']}/members/{c['subject']}"}}
        return {**{k: v for k, v in c.items() if not k.startswith("_")}, "card": _card(c, row, room, live),
                "call_live": live}

    # ── the card's answer ───────────────────────────────────────────────────────────────────────

    def _add_member(request: Request, subject: str, row: dict, room: dict, who: str, *, tell: bool) -> dict:
        s, root = _store(), _root()
        fresh = rm.add(s, root, row["id"], who, actor=subject)
        name, by = _name(who), _name(subject, request.headers.get("x-user-email") or "")
        if fresh:
            _say(row["id"], f"{by} added {name} to #{row.get('title') or 'this room'}.")
            if tell and notifier is not None:
                notifier.emit([who], kind="room_added", actor=subject, actor_name=by, object_kind="chat",
                              object_id=row["id"], object_name=row.get("title") or "a room")
                notifier.rail([who], session_id=row["id"], change="added")
        call = _live_call(row["id"])
        rang = (room_ring.ring(s, root, notifier, row["id"], caller=subject, caller_name=by,
                               title=row.get("title") or "", only=[who], call_room=call["room"])
                if call and fresh else [])
        return {"added": fresh, "subject": who, "name": name, "rang": rang}

    def _propose_always(request: Request, subject: str, row: dict, ws: str) -> dict:
        """"Yes, and always for this workspace" — one readable line in the workspace's rules, proposed
        to its owner (I15). The owner's own click from a browser session applies it at once."""
        s, root = _store(), _root()
        try:
            rules = m.read_rules(root, ws)
        except m.MembershipError as exc:
            raise HTTPException(status_code=503, detail={"refused": pd.POLICY_UNREADABLE, "said": str(exc)}) from None
        line = {"id": f"rooms.mention-adds.{subject}", "effect": pd.ALLOW, "action": MENTION_ADDS,
                "principals": [subject], "type": "grant",
                "text": (f"In {_ws_name(ws)}, when {_name(subject)} @mentions a workspace member who isn't in "
                         "the room, add them and tell them.")}
        if any(r.get("id") == line["id"] for r in rules if isinstance(r, dict)):
            return {"line": line, "already": True}
        value = pp.normalize("rules", [*rules, line])
        owner = pp.owner_of(root, ws)
        if not owner:
            raise HTTPException(status_code=409, detail="this workspace has no owner to decide")
        p = s.propose(row["id"], owner=owner, kind="policy", workspace=ws, title=f"rules: {ws}"[:300],
                      body=pp.body_of("rules", value, reason="“Always” on a tag card in a room",
                                      on_behalf_of=pp.proposer_of(request.headers, subject)))
        applied = False
        if owner == subject and pd.credential_of(request.headers) == pd.SESSION:
            try:
                pp.apply(root, p, approver=subject, approver_email=request.headers.get("x-user-email") or "",
                         credential=pd.SESSION, index=mindex)
                s.decide(p["id"], subject, accept=True)
                applied = True
            except m.MembershipError as exc:
                logger.warning("always line not applied (%s)", exc)
        return {"line": line, "proposal": p["id"], "applied": applied, "decides": owner}

    @router.post("/api/rooms/{session}/members")
    def room_member_add(session: SessionPath, request: Request, body: AddBody = Body(...)):
        """THE TAG CARD'S ANSWER (a person's click; refused to every delegated caller). A workspace
        member is added (and told, and rung while a call is live); ``always`` also proposes the
        workspace line. Anyone else gets a workspace invite bound to their address, with this room as
        pending membership — the same answer whether or not the address has an account (M1)."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Adding someone to a room is a person's click on the card. Show the card and stop.")
        if not (body.subject or body.email):
            raise HTTPException(status_code=422, detail="name a subject or an email")
        if body.always and body.origin == "agent":
            raise HTTPException(status_code=403, detail={"refused": "agent_mentions_never_always",
                                                         "said": "An agent's mention never sets “always”."})
        subject, row, room = _room_row(request, session)
        s = _store()
        if not s.can_write(row["id"], subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        c = _classify(request, subject, row, room, body)
        if c["case"] == "member":
            return {**c, "added": False}
        if c["case"] in ("pair_full", "unknown"):
            raise HTTPException(status_code=409, detail={"refused": c["case"], "said": c["said"]})
        if c["case"] == "workspace_member":
            out = _add_member(request, subject, row, room, c["subject"], tell=body.tell)
            if body.always:
                out["always"] = _propose_always(request, subject, row, room.get("workspace") or "")
            return {**c, **out}
        # cases 3 and 4: the invite — minted only for a person's session (H3); the card is that signature
        require_session_credential(request)
        # (the guest seat stays refused by the floor until it exists)
        if c["seat"] == "guest":                                 # unreachable while the floor holds
            raise HTTPException(status_code=501, detail={"refused": "guest_seats_not_built"})
        try:
            answer = membership_acts.invite(
                _root(), room.get("workspace") or "", email=c["_address"], role=c["role"], inviter=subject,
                inviter_email=request.headers.get("x-user-email") or "",
                workspace_name=c.get("workspace_name") or "", index=mindex,
                ui_url=(settings.ui_url if settings is not None else ""), commit_fn=_pc,
                resolve_subject=lookup, mail=publish_mod.publish_invite if body.tell else None,
                mail_delivery=publish_mod.mail_delivery, owner_act=False, room=row["id"])
        except membership_acts.ActRefused as exc:
            raise HTTPException(status_code=exc.status, detail={"refused": "invite_refused", "said": str(exc)}) from None
        except m.MembershipError as exc:
            raise HTTPException(status_code=exc.status, detail={"refused": "invite_refused", "said": str(exc)}) from None
        if answer.get("invited") and answer.get("internal") and body.tell and notifier is not None:
            try:
                invitee = lookup(c["_address"]) if lookup is not None else None
                if invitee:
                    notifier.emit([str(invitee)], kind="workspace_invited", actor=subject,
                                  actor_name=_name(subject), object_kind="workspace",
                                  object_id=room.get("workspace") or "", object_name=c.get("workspace_name") or "",
                                  url=answer.get("link"))
            except Exception:  # noqa: BLE001 — the invite exists; the notice is the extra
                pass
        _say(row["id"], f"{_name(subject)} invited {c['name']} to #{row.get('title') or 'this room'}.")
        # the same answer for an address with an account and one without (M1): no `internal`, no `delivery` word
        return {"case": c["case"], "invited": bool(answer.get("invited")), "email": c["email"],
                "role": answer.get("role"), "expires_at": answer.get("expires_at"),
                "invite_id": answer.get("invite_id"), "link": answer.get("link")}

    @router.delete("/api/rooms/{session}/members/{principal}")
    def room_member_remove(session: SessionPath, principal: Annotated[str, Path(pattern=_PRINCIPAL)],
                           request: Request):
        """A closed room's owner takes someone out (the Undo of an add). An open room's members are its
        workspace's: there, only the person's own following of the room ends."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Removing someone from a room is the owner's act. Suggest it and stop.")
        subject, row, room = _room_row(request, session)
        s = _store()
        if principal != subject and s.role_of(row["id"], subject) != "owner":
            raise HTTPException(status_code=403, detail={"refused": "not_session_owner"})
        if s.role_of(row["id"], principal) == "owner":
            raise HTTPException(status_code=403, detail={"refused": "owner_role_fixed"})
        removed = rm.remove(s, _root(), row["id"], principal)
        if removed and notifier is not None:
            notifier.rail([principal], session_id=row["id"], change="removed")
        return {"removed": removed, "session_id": row["id"]}

    @router.get("/api/rooms/{session}/people")
    def room_people_panel(session: SessionPath, request: Request):
        """THE MEMBERS PANEL: who is in the room (with how they got there and whether they opened it),
        who is invited and not on Vexa yet, and — for a closed room — the workspace's members who are
        not here (each with Add). Names only; never another person's address (M1)."""
        subject, row, room = _room_row(request, session)
        s, root = _store(), _root()
        try:
            mem = rm.members(s, root, row["id"])
        except m.MembershipError as exc:
            raise HTTPException(status_code=503, detail={"refused": "policy_unreadable", "said": str(exc)}) from None
        here = {x["principal"] for x in mem}
        ws = room.get("workspace") or ""
        others: list = []
        if ws and not rm.is_open(room) and not pair_rooms.is_pair(root, ws):
            try:
                others = [{"principal": str(r["subject"]), "name": _name(str(r["subject"]))}
                          for r in m.read_members(root, ws)
                          if r.get("subject") and str(r["subject"]) not in here and r.get("discoverable") is not False]
            except m.MembershipError:
                others = []
        invited = []
        try:
            for inv in m.list_invites(root, ws) if ws and not pair_rooms.is_pair(root, ws) else []:
                if ((inv.get("room") or {}).get("id") if isinstance(inv.get("room"), dict) else inv.get("room")) == row["id"] and not inv.get("revoked") and int(inv.get("uses") or 0) == 0:
                    invited.append({"invite_id": inv.get("id"), "expires_at": inv.get("expires_at"),
                                    "label": ", ".join(inv.get("allowed_emails") or []) if s.role_of(row["id"], subject) == "owner"
                                    else "someone invited", "by": _name(str(inv.get("created_by") or ""))})
        except Exception:  # noqa: BLE001 — the panel shows what it can
            invited = []
        return {"session_id": row["id"], "you": subject, "workspace": ws, "workspace_name": _ws_name(ws),
                "visibility": room.get("visibility"), "pair": pair_rooms.is_pair(root, ws) if ws else False,
                "members": [{**x, "name": _name(x["principal"]),
                             **({"added_by_name": _name(x["added_by"])} if x.get("added_by") else {})} for x in mem],
                "invited": invited, "workspace_others": others}

    # ── rooms of two (FD4) ──────────────────────────────────────────────────────────────────────

    @router.post("/api/rooms/direct")
    def room_direct(request: Request, body: DirectBody = Body(...)):
        """"MESSAGE <PERSON>": the room of two with them — created once, the same pair always landing in
        the same room, backed by a hidden pair workspace. Only someone the caller shares a workspace
        with (anyone else is not found, as an unknown id would be); a person with no account is invited
        instead (the tag flow)."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Opening a conversation is the person's act. Suggest it and stop.")
        subject = str(subject_of(request))
        other = str(body.subject)
        if other == subject:
            raise HTTPException(status_code=422, detail="a room of two is with someone else")
        root = _root()
        try:
            if not people_directory.may_mention(root, subject, other, _index_list):
                raise HTTPException(status_code=404, detail="person not found")
            sid, created = pair_rooms.open_room(_store(), root, subject, other, title="",
                                                opener_email=request.headers.get("x-user-email"), commit_fn=_pc)
        except m.MembershipError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        if created and notifier is not None:
            notifier.rail([subject, other], session_id=sid, change="added")
        return {"session_id": sid, "created": created, "with": {"subject": other, "name": _name(other)},
                **rooms.settings(_store(), sid)}

    # ── the two switches (7hh) ──────────────────────────────────────────────────────────────────

    def _switch_error(exc: room_switches.SwitchRefused):
        return HTTPException(status_code=exc.status, detail=exc.detail)

    def _switch_view(subject: str, row: dict, room: dict, request: Request) -> dict:
        root = _root()
        ws = room.get("workspace") or ""
        pair = pair_rooms.is_pair(root, ws) if ws else False
        owner = _store().role_of(row["id"], subject) == "owner" or (
            bool(ws) and (m.is_member(root, ws, subject) == "owner"))
        asks = room_switches.state(room)["asks"]
        return {"session_id": row["id"], **{k: v for k, v in room_switches.state(room).items() if k != "asks"},
                "asks": {k: {**a, "mine": str(a.get("by")) == subject, "by_name": _name(str(a.get("by")))}
                         for k, a in asks.items()},
                "pair": pair, "can_switch": pair or owner,
                "locks": room_switches.locks(root, ws, subject, credential=pd.credential_of(request.headers))}

    @router.get("/api/rooms/{session}/switches")
    def switches_get(session: SessionPath, request: Request):
        subject, row, room = _room_row(request, session)
        return _switch_view(subject, row, room, request)

    @router.post("/api/rooms/{session}/switches")
    def switches_set(session: SessionPath, request: Request, body: SwitchBody = Body(...)):
        """Turn a switch on or off. In a room of two, off is either person's; on is an ask the other
        person answers on a card. In a bigger room its owners switch. A policy lock refuses (423)."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="The room's switches are its people's. Say what you would change and stop.")
        subject, row, room = _room_row(request, session)
        s, root = _store(), _root()
        try:
            out = room_switches.change(s, root, row["id"], subject, body.switch, body.on,
                                       credential=pd.credential_of(request.headers))
        except room_switches.SwitchRefused as exc:
            raise _switch_error(exc) from None
        word = room_switches.WORDS[body.switch]
        if out.get("applied"):
            _say(row["id"], f"{word.capitalize()} switched {'on' if body.on else 'off'} by {_name(subject)}.")
        elif out.get("asked"):
            _say(row["id"], f"{_name(subject)} asked to turn {word} back on · waiting for the other person.")
            if notifier is not None:
                notifier.emit([out["asked"]], kind="room_switch_ask", actor=subject, actor_name=_name(subject),
                              object_kind="chat", object_id=row["id"], object_name=row.get("title") or "your room")
        return {**out, **_switch_view(subject, row, s.room(row["id"]) or room, request)}

    @router.post("/api/rooms/{session}/switches/{switch}/answer")
    def switches_answer(session: SessionPath, switch: Literal["vexa", "transcribe"], request: Request,
                        body: AnswerBody = Body(...)):
        """The other person's answer to a turn-on ask (the card): Turn on · Keep off."""
        refuse_delegated(request, reason="room_needs_a_person",
                         instruction="Consent is the person's click. Show the card and stop.")
        subject, row, room = _room_row(request, session)
        s = _store()
        try:
            out = room_switches.answer(s, _root(), row["id"], subject, switch, body.accept)
        except room_switches.SwitchRefused as exc:
            raise _switch_error(exc) from None
        word = room_switches.WORDS[switch]
        _say(row["id"], f"{word.capitalize()} switched back on — both agreed." if body.accept
             else f"{_name(subject)} kept {word} off.")
        return {**out, **_switch_view(subject, row, s.room(row["id"]) or room, request)}

    # ── D10: shared chats become rooms, once ────────────────────────────────────────────────────

    @router.post("/internal/rooms/migrate-shared-chats")
    def migrate_shared_chats(request: Request, dry_run: bool = True):
        """THE ONE-TIME MIGRATION (D10, §10 Q5) — internal tier only. ``dry_run`` (the default) reports
        what each shared chat would become; otherwise it migrates and tells each person once."""
        if _internal_caller is None or not _internal_caller(request):
            raise HTTPException(status_code=403, detail={"refused": "internal_tier_required"})
        from control_plane import shared_chat_migration as scm  # noqa: PLC0415

        def target_of(owner: str, name: str) -> str:
            return sess.target(owner, name) if sess is not None else ""

        if dry_run:
            return {"dry_run": True, **scm.plan(_store(), target_of)}

        def notify(people: list, actor: str, sid: str, text: str) -> None:
            if notifier is not None:
                notifier.emit(people, kind="chats_migrated", actor=actor, actor_name="Vexa",
                              object_kind="chat", object_id=sid, object_name=text)
                notifier.rail(people, session_id=sid, change="added")

        return {"dry_run": False, **scm.apply(_store(), _root(), target_of, notify=notify)}

    return router
