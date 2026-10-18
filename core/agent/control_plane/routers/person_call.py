"""routers/person_call.py — "CALL <PERSON>": a Vexa call with someone, started from the person's chat.

    POST /api/rooms/call    call a person (or an existing room): tool ``call_person``

Founder bug 2026-10-18: in his own chat he typed "Call tester-b" and the agent answered that Vexa
cannot place a call, only send a transcription bot into a meeting that already has a link, and
offered a Google Calendar Meet link. Vexa HAS its own calls (a LiveKit room per session, ADR-0048
decision 6), and a room of two is the place a call with one person happens (UI 7hh, FD4). The agent
had no verb that composes them, so it improvised. This route is that verb, built only from pieces
that already exist:

1. WHO. ``person`` is resolved in the caller's DIRECTORY only (``people_directory.mentionable``: the
   humans who share a group workspace with them, UI 7ii). Nobody else is ever matched or named. No
   match is a 404 the agent turns into one short question; several are a 409 naming the candidates
   (names and shared workspaces, never an address: M1).
2. WHERE. The room of two with them, found or created once (``pair_rooms.open_room``: the hidden pair
   workspace, both owners, the opener is ``created_by`` so the room bills the opener, I20). Or
   ``room``: an existing room the caller writes in.
3. THE CALL. meeting-api makes it (``POST /bots {"platform": "vexa_call", "session_id"}``, plus the
   starter's transcript share), called WITH THE CALLER'S OWN SIGNED IDENTITY forwarded unchanged, so
   meeting-api applies the caller's own regime, scopes and bot limit, and the meeting is theirs.
   "Transcribe calls" off in the room starts it untranscribed. Then it is bound to the room exactly
   as the Call button binds it (``store.bind_call``), and its recap lands in the room.
4. WHO HEARS. The other person is rung (``room_ring``: a live ring frame and a ``call_started``
   notice that expires) — in-app only. Nothing is mailed from here.
5. THE ANSWER. The room and the call, as references the chat renders, so the caller opens it.

THE GUARDS, in order: a person in the loop (``person: true`` in routes.v1, and here); never a turn
other people read, never a room's workspace principal; the ``rooms`` and ``calls`` features; the
policy evaluator (``call.person``, D7: the floor denies a workspace principal, a workspace can deny
it); and the privacy hold (D6): the ring is a notice to another person, so a session marked with
private sources is held on the server-composed ring line exactly like any notice, with a confirm
code. The body carries NO free text: nothing the model writes reaches the other person.

DELIBERATE, AND SAID: S4's ``POST /api/rooms/direct`` refuses every delegated caller ("opening a
conversation is the person's act"). Here the person's own words this turn ARE the act ("Call
tester-b"), so a watched agent may open the room of two as part of the call; an unwatched one never.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane import features as features_mod
from control_plane import identity_token, pair_rooms, people_directory, policy_decide as pd
from control_plane import private_taint, room_ring, room_switches, session_sync
from control_plane import workspace_membership as m
from control_plane.bodies import CHAT_SESSION_PATTERN
from control_plane.ceiling import is_delegated, refusal, refused, require_in_ceiling

logger = logging.getLogger("agent_api.person_call")

#: The policy action (D7). Allowed by default to a person and to their watched agent; the floor denies
#: a workspace principal; a workspace rule may deny it in its rooms.
ACTION = "call.person"
#: How long the share that lets the call's participants read its transcript lives (the call's cap).
CALL_SHARE_TTL_SEC = 6 * 3600
#: The ring is answered from the notice for this long; a call nobody joins ends with it.
RING_WINDOW_S = 30
#: The answer asks the caller's own tab to join the call it just started (`llm.tool_events` turns it
#: into a `call` event; the terminal opens the room and joins as the caller). Without that join the
#: call ends after meeting-api's first-join window if the caller is slow to open the room.
JOIN_CALL = "join_call"


class CallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: Optional[str] = Field(
        default=None, min_length=1, max_length=200,
        description="who to call: the account id an @-mention carries, their name as the person said it "
                    "(\"tester-b\", \"Maya\"), or their address. Only someone the person shares a "
                    "workspace with is ever matched.")
    room: Optional[str] = Field(
        default=None, pattern=CHAT_SESSION_PATTERN,
        description="instead of `person`: start the call in this existing room (its id or name) and "
                    "ring its members.")


class CallRefused(Exception):
    def __init__(self, status: int, detail) -> None:
        super().__init__(str(detail))
        self.status, self.detail = status, detail


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lstrip("@").lower().split())


def match_people(people: list[dict], asked: str) -> tuple[str, list[dict]]:
    """``("one" | "many" | "none", rows)`` for what the person said, against their directory only.

    Tiers, the first that matches decides: the account id; a whole name or a whole address or its
    local part ("tester-b"); a name that starts with what was said, word by word ("Maya" → "Maya
    Ortiz", "maya o" → "Maya Ortiz"). Several rows on the deciding tier is ``many`` — never a guess."""
    q = _norm(asked)
    if not q:
        return "none", []
    exact_id = [p for p in people if str(p.get("subject")) == str(asked).strip().lstrip("@")]
    if exact_id:
        return "one", exact_id[:1]

    def local(p: dict) -> str:
        return _norm(str(p.get("email") or "").split("@", 1)[0])

    whole = [p for p in people
             if q in {_norm(p.get("name") or ""), _norm(p.get("email") or ""), local(p)} - {""}]
    if whole:
        return ("one" if len(whole) == 1 else "many"), whole
    words = q.split()

    def starts(p: dict) -> bool:
        for label in (_norm(p.get("name") or ""), local(p)):
            toks = label.replace(".", " ").replace("_", " ").split()
            if len(toks) >= len(words) and all(t.startswith(w) for t, w in zip(toks, words)):
                return True
        return False

    part = [p for p in people if starts(p)]
    if part:
        return ("one" if len(part) == 1 else "many"), part
    return "none", []


def _http(method: str, url: str, headers: dict, body: Optional[dict] = None, timeout: float = 10.0):
    """``(status, json | text)``; ``(0, reason)`` when the service could not be reached."""
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "null"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw
    except Exception as exc:  # noqa: BLE001 — unreachable is a typed answer, never a raise
        return 0, type(exc).__name__


def meeting_api_starter(base: str, http: Callable = _http) -> Callable:
    """Start a call on meeting-api AS THE CALLER: their signed identity (gateway-identity.v1
    ``X-Vexa-Identity``, verified on the way in) forwarded unchanged, so meeting-api applies the
    caller's own regime, scopes and bot limit, and owns the meeting as theirs. Returns
    ``start(identity, session_id, transcribe) -> {room, meeting_id, share_token, transcribed}``."""
    base = base.rstrip("/")

    def start(identity: str, session_id: str, transcribe: bool) -> dict:
        h = {identity_token.HEADER: identity}
        body = {"platform": "vexa_call", "session_id": session_id, **({} if transcribe else {"transcribe": False})}
        code, out = http("POST", f"{base}/bots", h, body)
        if not 200 <= int(code or 0) < 300 or not isinstance(out, dict) or not out.get("native_meeting_id"):
            raise CallRefused(502 if not code else (int(code) if int(code) >= 400 else 502),
                              {"refused": "call_not_started", "meetings": out if isinstance(out, (dict, str)) else None})
        room = str(out["native_meeting_id"])
        mid = out.get("id")
        if not transcribe or mid is None:
            return {"room": room, "meeting_id": None, "share_token": "", "transcribed": False}
        code, share = http("POST", f"{base}/meetings/{int(mid)}/share", h,
                           {"mode": "open", "expires_in_sec": CALL_SHARE_TTL_SEC})
        if not 200 <= int(code or 0) < 300 or not isinstance(share, dict) or not share.get("token"):
            raise CallRefused(502, {"refused": "call_share_not_minted"})
        return {"room": room, "meeting_id": int(mid), "share_token": str(share["token"]), "transcribed": True}

    return start


def build(**d) -> APIRouter:
    router = APIRouter()
    store = d.get("session_store")
    subject_of = d["subject_of"]
    wsr, mindex = d.get("wsr"), d.get("mindex")
    notifier = d.get("notifier")
    settings = d.get("settings")
    livekit = d.get("livekit")
    sess = d.get("sess")
    _pc = d.get("_pc")
    _global_root = d.get("_global_root")
    _meeting_annotator = d.get("_meeting_annotator")
    starter = d.get("call_starter")          # tests: a fake meeting-api

    def _index_list(who: str) -> list:
        return list(mindex.list(who) or []) if mindex is not None else []

    def _root():
        if wsr is None:
            raise HTTPException(status_code=503, detail={"refused": "workspaces_off"})
        return wsr.root

    def _store():
        if store is None:
            raise HTTPException(status_code=503, detail={"refused": "session_store_off"})
        return store

    def _name(who: str, email: str = "") -> str:
        try:
            return people_directory.name_of(_root(), str(who), _index_list, email) or str(who)
        except Exception:  # noqa: BLE001 — a name is a courtesy
            return str(who)

    def _start() -> Callable:
        if starter is not None:
            return starter
        base = (getattr(settings, "meeting_api_url", "") or "") if settings is not None else ""
        if not base or livekit is None:
            raise HTTPException(status_code=503, detail={
                "refused": "calls_not_deployed",
                "said": "Calls are not deployed here (no meeting service or no call server)."})
        return meeting_api_starter(base)

    def _feature_on(name: str, subject: str) -> bool:
        try:
            root = _global_root() if callable(_global_root) else _global_root
        except Exception:  # noqa: BLE001
            root = None
        try:
            return bool(features_mod.effective(root, subject=subject).get(name))
        except Exception:  # noqa: BLE001 — a switch that cannot be read is off
            return False

    def _guard(request: Request) -> str:
        """Who calls, or the refusal: a person in the loop, in a turn nobody else reads."""
        h = request.headers
        if identity_token.is_unwatched(h):
            raise refused(request, 403, identity_token.REFUSAL, reason=identity_token.REFUSAL["reason"])
        if identity_token.is_shared(h) or identity_token.is_workspace_principal(h):
            raise refused(request, 403, refusal(
                "call_from_your_own_chat",
                "A call with a person is started from the caller's own chat, or with the room's Start "
                "call button. Say so in one line and stop."), reason="call_from_your_own_chat")
        subject = str(subject_of(request))
        for feature in ("rooms", "calls"):
            if not _feature_on(feature, subject):
                raise HTTPException(status_code=403, detail=features_mod.refusal(feature, for_account=True))
        return subject

    def _decide(request: Request, subject: str, workspace: str) -> None:
        out = pd.decide(pd.Ask(principal=subject, action=ACTION, workspace=workspace,
                               credential=pd.credential_of(request.headers),
                               principal_kind="ws" if identity_token.is_workspace_principal(request.headers)
                               else "person"), root=_root() if workspace else None)
        if out.decision == pd.REFUSE:
            raise HTTPException(status_code=503, detail={"refused": pd.POLICY_UNREADABLE, "rule": out.rule,
                                                         "said": "The policy cannot be read, so no call was started."})
        if not out.allowed:
            raise HTTPException(status_code=403, detail={"refused": "call_denied_by_policy", "rule": out.rule,
                                                         "said": out.reason or "A policy line does not allow this call."})

    def _resolve_person(subject: str, asked: str) -> dict:
        try:
            people = people_directory.mentionable(_root(), subject, _index_list)
        except Exception:  # noqa: BLE001 — an unreadable directory names nobody
            people = []
        verdict, rows = match_people(people, asked)
        if verdict == "one":
            return rows[0]
        if verdict == "many":
            names = {}
            try:
                for p in rows:
                    names[p["subject"]] = [str(w) for w in p.get("workspaces") or []]
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(status_code=409, detail={
                "refused": "ambiguous_person",
                "candidates": [{"subject": p["subject"], "name": p.get("name") or p["subject"],
                                "workspaces": names.get(p["subject"], [])} for p in rows[:8]],
                "instruction": "Ask ONE short question naming these people (by name and a shared workspace), "
                               "then call call_person again with `person` = the chosen account id."})
        raise HTTPException(status_code=404, detail={
            "refused": "not_in_directory",
            "instruction": "Nobody the person shares a workspace with matches that. Ask ONE short question: who "
                           "they mean (a name as it appears in People) or, for someone outside their workspaces, "
                           "their email so you can offer workspace_invite. Never offer a calendar or Meet link "
                           "instead."})

    def _live(session_id: str) -> Optional[dict]:
        s = _store()
        call = s.active_call(session_id)
        if call is None:
            return None
        try:
            if livekit is not None and livekit.room(call["room"]) is None:
                s.end_call(session_id, call["room"])
                return None
        except Exception:  # noqa: BLE001 — the call server unreachable: trust the record
            pass
        return call

    def _own_by_room(subject: str, session_id: str, room: dict, meeting_id) -> None:
        """A call started in a room is the room's: its recap lands in the room (calls.py's rule)."""
        if not meeting_id:
            return
        try:
            if sess is not None:
                sess.bind_meeting_chat(str(subject), str(meeting_id), session_id)
            if _meeting_annotator is not None:
                _meeting_annotator(str(subject), meeting_id,
                                   {"room": session_id, "workspace": room.get("workspace") or ""})
        except Exception as exc:  # noqa: BLE001 — the call is bound; its ownership record is the extra
            logger.warning("room ownership not recorded for call meeting %s (%s)", meeting_id, type(exc).__name__)

    @router.post("/api/rooms/call")
    def call_person(request: Request, body: CallBody = Body(...)):
        """CALL A PERSON: find them among the people you share a workspace with, open your room of
        two with them (made once), start a Vexa call there and ring them in the app. The answer
        names the room and the call as references to open it. ``room`` instead calls an existing room."""
        subject = _guard(request)
        if bool(body.person) == bool(body.room):
            raise HTTPException(status_code=422, detail="name exactly one of `person` or `room`")
        s, root = _store(), _root()
        _decide(request, subject, "")                       # the platform floor, before anything exists
        other: Optional[dict] = None
        created = False
        if body.person:
            other = _resolve_person(subject, body.person)
            if str(other["subject"]) == subject:
                raise HTTPException(status_code=422, detail="that is you")
            try:
                sid, created = pair_rooms.open_room(s, root, subject, str(other["subject"]), title="",
                                                    opener_email=request.headers.get("x-user-email"), commit_fn=_pc)
            except m.MembershipError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from None
            if created and notifier is not None:
                notifier.rail([subject, str(other["subject"])], session_id=sid, change="added")
        else:
            row = session_sync.resolve(s, subject, str(body.room), create=False)
            if row is None:
                raise HTTPException(status_code=404, detail="room not found")
            sid = row["id"]
        room = s.room(sid)
        if room is None:
            raise HTTPException(status_code=409, detail={"refused": "not_a_room",
                                                         "said": "That is a private chat, not a room."})
        if not s.can_write(sid, subject):
            raise HTTPException(status_code=403, detail="session_read_only")
        ws = str(room.get("workspace") or "")
        if ws and not pair_rooms.is_pair(root, ws):
            require_in_ceiling(request, ws)                 # a named room: inside the dispatch's ceiling
        _decide(request, subject, ws)                       # the room's workspace may deny calls in it
        everyone = [h for h in s.humans(sid) if h != subject]
        ring_to = [str(other["subject"])] if other is not None else everyone
        caller_name = _name(subject, request.headers.get("x-user-email") or "")
        # THE PRIVACY HOLD (D6): a ring is a notice to other people. Its words are the server's own,
        # so a session whose private reads were all fingerprinted passes; anything else waits for the
        # person's `confirm <code>`, exactly as a notice would.
        ring_line = f"{caller_name} is calling you"
        held = private_taint.check_egress(
            subject=subject, unit=identity_token.session_of(request.headers), surface="call",
            target=f"call/{sid}", readers=ring_to, path="the call's ring", content=ring_line,
            delegated=is_delegated(request))
        if held is not None:
            raise HTTPException(status_code=409, detail=held)
        with_ = ({"subject": str(other["subject"]), "name": other.get("name") or _name(str(other["subject"]))}
                 if other is not None else None)
        live = _live(sid)
        if live is not None:
            rang = room_ring.ring(s, root, notifier, sid, caller=subject, caller_name=caller_name,
                                  title=room.get("title") or "", only=ring_to, call_room=live["room"])
            return {"session_id": sid, "room_ref": f"[[room:{sid}]]", "room_created": created, "with": with_,
                    "ui_action": JOIN_CALL, "already_live": True,
                    "call": {"room": live["room"], "meeting_id": live.get("meeting_id") or None},
                    "rang": rang,
                    "say": "A call is already live in that room; they were rung again. Give the room reference "
                           "so the person can open it and join."}
        identity = (request.headers.get(identity_token.HEADER) or "").strip()
        if not identity and starter is None:
            raise HTTPException(status_code=503, detail={
                "refused": "identity_not_forwardable",
                "said": "This request carries no signed identity to start the call with."})
        transcribe = room_switches.transcribe_on(s, sid)
        try:
            started = _start()(identity, sid, transcribe)
        except CallRefused as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None
        call = s.bind_call(sid, room=started["room"], meeting_id=started["meeting_id"] or 0, owner=subject,
                           share_token=started["share_token"] or "")
        _own_by_room(subject, sid, room, started["meeting_id"])
        rang = room_ring.ring(s, root, notifier, sid, caller=subject, caller_name=caller_name,
                              title=room.get("title") or "", only=ring_to, call_room=started["room"])
        who = with_["name"] if with_ else "the room"
        return {"session_id": sid, "room_ref": f"[[room:{sid}]]", "room_created": created, "with": with_,
                "ui_action": JOIN_CALL, "already_live": False,
                "call": {"room": started["room"], "meeting_id": started["meeting_id"],
                         "transcribed": started["transcribed"], "started": call.get("started") if call else time.time()},
                "rang": rang, "ring_window_s": RING_WINDOW_S,
                "say": (f"Calling {who}. Say that in one line with the room reference {{room_ref}} so the person "
                        f"opens it and joins; it rings for {RING_WINDOW_S} s. If `rang` is empty, nobody was "
                        "rung: say so.").replace("{room_ref}", f"[[room:{sid}]]")}

    return router
