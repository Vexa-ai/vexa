"""routers/notifications.py — a person's notifications (#1783): list them, mark them read.

The rows are the caller's own and nobody else's (``control_plane/notifications.py``): every query
is keyed by the signed-in subject, so no parameter can name another person. ``503`` when the
session store is not configured — notifications live in its database.

These are also the agent's tools, ``notifications_list`` and ``notifications_mark_read``, so a
person can ask the chat *"what did I miss?"* and the agent reads the same list the bell shows.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

import re

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import notifications as notifications_mod
from control_plane.bodies import CHAT_SESSION_PATTERN
from control_plane.identity_token import DELEGATION_HEADERS, session_of

NOT_CONFIGURED = "Notifications are not configured on this deployment"


class MarkReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: Optional[list[Annotated[str, Field(pattern=r"^n-[0-9a-f]{16}$")]]] = Field(
        default=None, max_length=200, description="the notifications to mark read, by id")
    all: bool = Field(default=False, description="mark every unread notification read")


class NotifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=notifications_mod.AGENT_TEXT_MAX,
                      description="one line naming the act and the object, e.g. \"3 emails need a reply today: "
                                  "the bank, the landlord, the school\". Never paste content.")
    session: Optional[str] = Field(default=None, pattern=CHAT_SESSION_PATTERN,
                                   description="the current chat session from the turn context; the notice links to it")
    to: Optional[list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,199}$")]]] = Field(
        default=None, max_length=20,
        description="account ids of people in THIS chat to notify instead of the owner; omit for the owner")
    link: Optional[str] = Field(default=None, max_length=300,
                                description="what a click opens instead of the chat: `page:<workspace>/<path>` "
                                            "or `meeting:<id>` (a meeting link goes to the owner only)")


class ActBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^n-[0-9a-f]{16}$", description="the notification's id from notifications_list")
    action: Literal["open", "accept", "decline", "join"] = Field(
        description="one of the notification's own `actions`: accept or decline a workspace invite, join or "
                    "decline a call, or open its object")


class InternalNoticeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["meeting_shared", "bot_scheduled", "bot_joined", "card_waiting", "card_slipped", "delivery_failed"]
    to: Optional[str] = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._@+:-]{0,199}$")
    email: Optional[str] = Field(default=None, max_length=254)
    actor: Optional[str] = Field(default=None, max_length=200)
    actor_name: Optional[str] = Field(default=None, max_length=254)
    meeting_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    #: the object a nudge kind names (a card id); meeting kinds keep `meeting_id`
    object_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    title: Optional[str] = Field(default=None, max_length=1000)
    object_kind: Optional[Literal["meeting", "card", "none"]] = None
    #: Where Open goes: a path in this app (``/…``) only — never another origin.
    url: Optional[str] = Field(default=None, max_length=600, pattern=r"^/([^/\s][^\s]*)?$")


class InternalNoticeState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._@+:-]{0,199}$")
    ids: list[str] = Field(default_factory=list, max_length=200)


_LINK = re.compile(r"^(page):([A-Za-z0-9._-]{1,128})/([A-Za-z0-9._@+=/ -]{1,250})$|^(meeting):([0-9]{1,18})$")


def build(**d) -> APIRouter:
    router = APIRouter()
    notifier = d.get("notifier")
    subject_of = d["subject_of"]
    store = d.get("session_store")
    _global_root = d.get("_global_root")
    wsr = d.get("wsr")
    invite_doors = d.get("invite_doors") or {}

    def _n():
        if notifier is None:
            raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
        return notifier

    @router.get("/api/notifications")
    def list_notifications(request: Request,
                           unread: Annotated[bool, Query(description="only unread ones")] = False,
                           limit: Annotated[int, Query(ge=1, le=200)] = 50,
                           before: Annotated[Optional[float], Query(description="older than this time")] = None):
        """The caller's notifications, newest first, and how many are unread. Each names who did
        what to which object (a chat, a workspace, a meeting) — never a message's content."""
        return _n().listing(subject_of(request), unread_only=unread, limit=limit, before=before)

    @router.post("/api/notifications/read")
    def mark_read(body: MarkReadBody, request: Request):
        """Mark the caller's notifications read: the named ``ids``, or ``all``. An id that is not
        the caller's is neither marked nor reported."""
        if not body.all and not body.ids:
            raise HTTPException(status_code=422, detail="name ids, or all")
        return _n().mark_read(subject_of(request), body.ids, every=body.all)

    def _refuse(reason: str, status: int, message: str):
        return HTTPException(status_code=status, detail={"refused": reason, "message": message})

    @router.post("/api/notifications/notify")
    def notify(body: NotifyBody, request: Request):
        """Post a notification to the person you work for — their bell and a toast — when something
        needs their attention: an important email, a finished job, a decision waiting. One line
        naming the act and the object; never paste message content. It links to this chat (pass
        `session`) or to a page or meeting (`link`). `to` notifies other people IN THIS CHAT instead,
        never anyone else. At most 20 an hour, one per routine run; identical text within ten minutes
        is posted once. Off when the deployment's policy says `agent_notifications: off`."""
        from control_plane import session_sync, workspace_membership  # noqa: PLC0415

        n = _n()
        subject = subject_of(request)
        try:
            notifications_mod.agent_text(body.text)
        except notifications_mod.AgentRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if not notifications_mod.agent_notifications_on(_global_root() if _global_root else None):
            raise _refuse("agent_notifications_off", 403, "agent notifications are off on this deployment "
                                                          "(_global/POLICIES.md agent_notifications: off)")
        unit = session_of(request.headers)
        prefix = f"agent-{subject}-chat-"
        unit_session = ""
        if unit.startswith(prefix):
            unit_session = re.sub(r"(-g[0-9A-Za-z]+)?(-auto)?$", "", unit[len(prefix):])
        session = body.session or unit_session
        row = None
        if session:
            row = session_sync.resolve(store, subject, session, create=False) if store is not None else None
            if row is None:
                raise HTTPException(status_code=404, detail="session not found")
            if unit and unit_session and body.session and row["id"] != (
                    session_sync.resolve(store, subject, unit_session, create=False) or {}).get("id"):
                raise _refuse("not_this_chat", 403, "a worker notifies from its own chat only")
        people = set(store.humans(row["id"])) if row is not None else {subject}
        recipients = [str(x) for x in (body.to or [subject])]
        outside = [r for r in recipients if r not in people]
        if outside:
            raise _refuse("not_a_participant", 403, "`to` names only people in this chat")
        # THE PRIVACY HOLD (D6, `private_taint`): notice text to OTHER people from a session that
        # read the person's private data waits for the person.
        from control_plane import ceiling as _ceiling, private_taint  # noqa: PLC0415
        held = private_taint.check_egress(
            subject=str(subject), unit=unit, surface="notice",
            target=f"notice/{','.join(sorted(recipients))}",
            readers=[r for r in recipients if r != str(subject)], path="notice", content=body.text,
            delegated=_ceiling.is_delegated(request))
        if held is not None:
            raise HTTPException(status_code=409, detail=held)
        okind, oid, oname, url = ("chat", row["id"], row.get("title") or "a chat", None) if row is not None \
            else ("none", "agent", "", None)
        if body.link:
            m = _LINK.fullmatch(body.link.strip())
            if not m:
                raise HTTPException(status_code=422, detail="link is page:<workspace>/<path> or meeting:<id>")
            if m.group(1):
                ws, path = m.group(2), m.group(3)
                for r in recipients:
                    try:
                        ok = (wsr is not None and workspace_membership.is_member(wsr.root, ws, r) is not None) or r == subject
                    except Exception:  # noqa: BLE001 — an unreadable roster reads nothing
                        ok = False
                    if not ok:
                        raise _refuse("link_not_readable", 403, f"{r} cannot read that page; nothing was posted")
                okind, oid, oname, url = "page", f"{ws}/{path}", path.rsplit("/", 1)[-1], f"/w/{ws}/{path}"
            else:
                if recipients != [subject]:
                    raise _refuse("link_not_readable", 403, "a meeting link goes to the owner only")
                okind, oid, oname, url = "meeting", m.group(5), "a meeting", None
        regime = (request.headers.get(DELEGATION_HEADERS["regime"]) or "").strip().lower()
        flow = (store.get(row["id"]) or {}).get("flow") if row is not None and store is not None else None
        from control_plane.session_store import read_flow  # noqa: PLC0415
        bound = read_flow(flow)
        source_name = bound["flow"] if bound else "Your agent"
        run_unit = unit if (unit and regime not in ("", "human")) else ""
        try:
            got = n.agent_post(subject, recipients, text=body.text, source=(row or {}).get("id", "agent"),
                               source_name=source_name, object_kind=okind, object_id=oid, object_name=oname,
                               url=url, run_unit=run_unit)
        except notifications_mod.AgentRefused as exc:
            raise _refuse(exc.reason, exc.status, str(exc)) from None
        return {**got, "said": (f"Notified {len(got['to'])} "
                                f"{'person' if len(got['to']) == 1 else 'people'}."
                                if got["to"] else "Already notified with the same text in the last ten minutes.")}

    @router.post("/api/notifications/act")
    def act(body: ActBody, request: Request):
        """Do what a notification offers (`actions` on each row), as the person it was sent to:
        `accept` or `decline` a workspace invite (the same act as the join page — on accept the
        workspace is theirs at once), `join` or `decline` a call in a chat, or `open` the object.
        Use it when the person says "accept the Acme invite" or "join the call". The answer names what
        to open next. Accepting, declining and joining need the person in the loop (a `person` verb
        in routes.v1.json, refused to a routine by the app-wide gate)."""
        n = _n()
        subject = subject_of(request)
        row = n.get(subject, body.id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such notification")
        if body.action not in row["actions"]:
            raise HTTPException(status_code=409, detail={"refused": "not_an_action", "actions": row["actions"],
                                                         "message": f"this notification offers {', '.join(row['actions']) or 'nothing'}"})
        obj = row["object"]
        to_open = {"kind": obj["kind"], "id": obj["id"], **({"name": obj["name"]} if obj.get("name") else {}),
                   **({"url": obj["url"]} if obj.get("kind") == "meeting" and obj.get("url") else {})}
        if obj.get("kind") == "chat" and "turn=" in (row.get("_url") or ""):
            to_open["turn"] = (row.get("_url") or "").split("turn=", 1)[1]
        acted = None
        if row["kind"] == "workspace_invited" and body.action in ("accept", "decline"):
            # THE ROW NAMES THE INVITE BY ID (PRD 0.13.4 M2); a row from before 0.13.4 still holds
            # the link, and its token is read as before until the boot scrub has rewritten it.
            stored = row.get("_url") or ""
            token, ref = "", ""
            if notifications_mod.is_invite_ref(stored):
                ref = stored
            else:
                m = re.search(r"[?&]i=([^&#]+)", stored)
                if m:
                    token = m.group(1)
            door = invite_doors.get(body.action)
            if not (token or ref) or door is None:
                raise HTTPException(status_code=409, detail={"refused": "not_an_action", "actions": [],
                                                             "message": "this invite can no longer be answered here"})
            if body.action == "accept":
                done = door(request, token, ceiling=False, ref=ref)
                acted = "accepted"
                to_open = {"kind": "workspace", "id": done.get("workspace_id") or obj["id"],
                           "name": done.get("name") or obj.get("name") or ""}
                if isinstance(done.get("room"), dict) and done["room"].get("id"):
                    to_open = {"kind": "chat", "id": done["room"]["id"],
                               "name": done["room"].get("title") or ""}
            else:
                door(request, token, ref=ref)
                acted, to_open = "declined", None
        elif row["kind"] == "call_started" and body.action == "join":
            acted, to_open = "joined", {"kind": "call", "id": obj["id"], "name": obj.get("name") or ""}
        elif row["kind"] == "call_started" and body.action == "decline":
            acted, to_open = "declined", None
        settled = n.settle(subject, body.id, acted)
        said = {"accepted": f"Joined {to_open and to_open.get('name') or 'the workspace'}.",
                "declined": "Declined.", "joined": "Joining the call."}.get(acted or "", "Opened.")
        return {"notification": settled, "open": to_open, "said": said}

    @router.post("/internal/notifications")
    def internal_notify(body: InternalNoticeBody, request: Request):
        """A meeting fact another domain holds, handed to the one writer (#1783) — INTERNAL TIER
        ONLY (`X-Internal-Secret`): flows, when it mails a meeting's share link (`meeting_shared`,
        to the account behind the address), sets a bot to join a calendar meeting
        (`bot_scheduled`) or hears a bot went live (`bot_joined`, both to the meeting's owner).
        Meeting kinds only; the text is composed here from the two names, like every other row. An
        address with no account is told nothing — the mail is their notice. Answers `{delivered}`."""
        internal = d.get("_internal_caller")
        if internal is None or not internal(request):
            raise HTTPException(status_code=403, detail="writing a notification from outside is an internal-tier capability")
        n = _n()
        recipient = str(body.to or "").strip()
        if not recipient and body.email:
            lookup = d.get("_email_subject_lookup")
            try:
                recipient = str((lookup(str(body.email).strip().lower()) if lookup else "") or "")
            except Exception:  # noqa: BLE001 — an unresolvable address is an address with no account
                recipient = ""
            if not recipient:
                return {"delivered": 0}
        if not recipient:
            raise HTTPException(status_code=422, detail="name the recipient: `to` (an account id) or `email`")
        nudge = body.kind in notifications_mod.NUDGE_KINDS
        okind = body.object_kind or ("card" if nudge else "meeting")
        oid = body.object_id if nudge else body.meeting_id
        if not oid or (nudge and okind != "card") or (not nudge and okind == "card"):
            raise HTTPException(status_code=422, detail="a meeting kind names meeting_id; a nudge kind names a card's object_id")
        rows = n.emit([recipient], kind=body.kind, actor=str(body.actor or "vexa"),
                      actor_name=body.actor_name or ("Vexa" if not body.actor else ""), object_kind=okind,
                      object_id=str(oid), object_name=body.title or "", url=body.url)
        return {"delivered": len(rows), "ids": [r["id"] for r in rows]}

    @router.post("/internal/notifications/state")
    def internal_notice_state(body: InternalNoticeState, request: Request):
        """READ STATE of one person's rows, for flows' delivery gate (PRD 0.13.4 S6, deny test 30): a
        mention read in the app is seen, and its pending email is suppressed. INTERNAL TIER ONLY.
        Answers ``{notices: {id: {read, acted}}}``; a row that is not that person's is absent."""
        internal = d.get("_internal_caller")
        if internal is None or not internal(request):
            raise HTTPException(status_code=403, detail="reading notification state is an internal-tier capability")
        return {"notices": _n().state(str(body.to), body.ids)}

    return router
