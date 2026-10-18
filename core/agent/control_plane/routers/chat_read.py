"""chat_read.py — an agent reads the person's own chats (founder, 2026-10-13).

``GET /api/chats/search`` and ``GET /api/chats/{session}/turns`` (MCP ``chats_search`` and
``chat_read``) serve the conversations the speaker can read — the session gate of ADR-0048
(participants and roles) — as the person's words and the agent's replies from each conversation's
turn records, never a raw harness transcript. Three narrowings, decided here and nowhere else:

* a SHARED turn (``delegation.shared``) reads only conversations every current participant of the
  conversation it answers into can read (the leak rule); anything else is ``shared_conversation``;
* a run with nobody in the loop (an unwatched regime: a routine, a background run) reads only the
  conversations its owner owns, never one they were merely added to;
* a person's own request reads everything they participate in.

Every chat the rail lists is readable by its owner: a chat recorded BEFORE the session store (it
has a row on the person's rail and its transcript in the old layout, and no row in the store) is
read from that layout — the caller's own only, never in a shared turn (nobody else can read it).
A turn reads as the person's words: the terminal's context line in front of them is machinery
(``chat_label.person_words``). An empty query lists the newest chats.

Every read is audited: one ``chat_read`` log line naming who read which conversation, how many
turns, and the agent session (unit) it was read from.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Path, Query, Request

from control_plane import identity_token, session_sync
from shared import chat_label as chat_label_mod

logger = logging.getLogger("agent_api.chat_read")

RECORDS = ".claude/sessions/conversation.turns.jsonl"
SEARCH_MAX_SESSIONS = 200
SNIPPET = 160
#: A chat as ``chat_read`` takes it: a session id, or the rail row ``chat_folders_list`` returns
#: (``c:<session>``), which is the same chat.
CHAT_REF_PATTERN = r"^(c:)?([A-Za-z0-9][A-Za-z0-9._-]{0,127})?$"


def _user(text) -> str:
    return chat_label_mod.person_words(str(text or ""))


def _from_history(hist) -> list[dict]:
    """Parsed transcript turns (``parse_history``) as read turns: the person's recorded words when a
    record names them, else the stored prompt without its machinery."""
    out = []
    for t in hist or []:
        if t.get("role") == "user":
            said = t.get("user_text")
            text = _user(said if isinstance(said, str) and said.strip() else t.get("text"))
        else:
            text = str(t.get("text") or "")
        if text:
            out.append({"role": t.get("role"), "text": text, **({"at": t["at"]} if t.get("at") else {})})
    return out


def _turns_of(files: dict) -> list[dict]:
    """The conversation as turns: the records' own words and replies; a conversation whose records
    carry no replies (written before they did) reads its transcript through the history parser."""
    out: list[dict] = []
    replies = False
    for line in (files.get(RECORDS) or b"").decode("utf-8", "replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or not _user(rec.get("user_text")):
            continue
        out.append({"role": "user", "text": _user(rec["user_text"]), "at": rec.get("ts")})
        if str(rec.get("reply") or "").strip():
            replies = True
            out.append({"role": "agent", "text": str(rec["reply"]), "at": rec.get("ts")})
    if replies or not files:
        return out
    try:
        hist = session_sync.history_from_files(files) or []
    except Exception:  # noqa: BLE001
        hist = []
    return _from_history(hist) or out


def _unit_session(unit: str, subject: str) -> str:
    """The conversation a delegated turn answers into, from its unit id
    (``agent-<subject>-chat-<session>[-g<gen>]``)."""
    prefix = f"agent-{subject}-chat-"
    if not unit.startswith(prefix):
        return ""
    return re.sub(r"-g[0-9A-Za-z]+(-auto)?$", "", unit[len(prefix):])


def _room_unit_session(unit: str) -> str:
    """The room a ROOM's turn answers in, from its unit id
    (``agent-ws-<w>-room-<session>[-read][-signal|-auto]``); ``""`` for any other unit."""
    from control_plane import room_echo  # noqa: PLC0415
    return room_echo.session_of(unit)


#: how much of a room call's transcript its room's agent reads back
ROOM_CALL_SEGMENTS = 400


def build(**d) -> APIRouter:
    router = APIRouter()
    store = d.get("session_store")
    subject_of = d["subject_of"]
    index = d.get("sess")
    wsr = d.get("wsr")
    mindex = d.get("mindex")
    redis_url = d.get("redis_url")

    def _incognito(subject: str, row: dict) -> bool:
        """An INCOGNITO chat (Vexa-ai/vexa#1783) is never read by another chat, nor found by search."""
        try:
            return index is not None and index.is_incognito(subject, str(row.get("name") or ""))
        except Exception:  # noqa: BLE001 — unreadable: treat as private, the safe side
            return True

    def _store():
        if store is None:
            raise HTTPException(status_code=503, detail="the session store is not configured")
        return store

    def _rail(subject: str) -> list[dict]:
        if index is None:
            return []
        try:
            return list(index.list(subject))
        except Exception:  # noqa: BLE001 — the store's chats still answer
            logger.warning("chat_read: the rail is unreadable subject=%s", subject)
            return []

    def _old_rows(subject: str) -> list[dict]:
        """The caller's chats on the rail that the store holds no row for — recorded before it."""
        s = _store()
        out = []
        for r in _rail(subject):
            name = str(r.get("session") or "")
            if not name or name.startswith(session_sync.STORE_ID_PREFIX):
                continue
            try:
                if s.by_name(subject, name) is not None:
                    continue
            except Exception:  # noqa: BLE001
                continue
            out.append({"id": name, "name": name, "owner": subject, "old_layout": True,
                        "title": chat_label_mod.strip_markers(r.get("named_title") or "") or r.get("title") or "",
                        "last_active": r.get("last_active")})
        return out

    def _old_row(subject: str, name: str) -> Optional[dict]:
        return next((r for r in _old_rows(subject) if r["name"] == name), None)

    def _old_turns(subject: str, name: str) -> list[dict]:
        """An old-layout chat's turns, from every root its transcript may sit in: the person's own
        tiers, the workspaces mounted for them, and the archive ``session_migrate --retire`` keeps."""
        if wsr is None:
            return []
        extra: list = []
        try:
            from control_plane import session_migrate  # noqa: PLC0415
            extra += session_migrate.archived_roots(wsr.root, subject)
        except Exception:  # noqa: BLE001
            pass
        try:
            from control_plane.workspace_attach import active_workspaces, shared_active_mounts  # noqa: PLC0415
            ms = active_workspaces(wsr.root, subject)
            if mindex is not None:
                ms += shared_active_mounts(wsr.root, subject, mindex.list(subject))
            extra += [m.path for m in ms]
        except Exception:  # noqa: BLE001 — the person's own tiers are still searched
            pass
        try:
            return _from_history(wsr.history(subject, name, extra_roots=extra))
        except Exception:  # noqa: BLE001 — an unreadable transcript reads as no turns
            logger.warning("chat_read: old-layout transcript unreadable subject=%s session=%s", subject, name)
            return []

    def _turns(subject: str, row: dict) -> list[dict]:
        if row.get("old_layout"):
            return _old_turns(subject, row["name"])
        return _turns_of(_store().load_bundle(row["id"]).get("files") or {})

    def _readers_of_here(request: Request, subject: str) -> Optional[set]:
        """In a shared turn, the people who read the conversation it answers into; None otherwise."""
        if not identity_token.is_shared(request.headers):
            return None
        unit = identity_token.session_of(request.headers)
        here = _unit_session(unit, subject)
        row = session_sync.resolve(_store(), subject, here, create=False) if here else None
        room_here = _room_unit_session(unit) if not here else ""
        if room_here:
            row = _store().get(room_here)
        if row is None:
            return {subject, "\x00nobody-else-known"}     # fail closed: nothing beyond the speaker's own
        return {p["principal"] for p in _store().participants(row["id"]) if p.get("kind") == "human"}

    def _own_room(request: Request, session: str) -> Optional[dict]:
        """The room this caller's turn is the ROOM's OWN agent in, when it is ``session``: a room's
        agent always reads its own room (everyone reading its answer is in it). None otherwise."""
        if not session or _room_unit_session(identity_token.session_of(request.headers)) != session:
            return None
        s = _store()
        row = s.get(session)
        try:
            return row if row is not None and s.room(row["id"]) is not None else None
        except Exception:  # noqa: BLE001 — a store that cannot say it is a room: not one
            return None

    def _room_extras(row: dict) -> dict:
        """What a room's own agent reads beyond the turns: the people's messages, and the
        transcript of every call held in the room."""
        s = _store()
        messages = [{"author": m["author"], "text": m["text"], "at": m["created"]}
                    for m in s.notes(row["id"])]
        calls = []
        try:
            held = s.calls_of(row["id"])
        except Exception:  # noqa: BLE001 — no call record reads as no calls
            held = []
        if held:
            from control_plane.api_shared import _fold_meeting_transcript  # noqa: PLC0415
            for c in held:
                calls.append({"meeting_id": c.get("meeting_id"), "started": c.get("started"),
                              "ended": c.get("ended"),
                              "transcript": _fold_meeting_transcript(redis_url, str(c.get("meeting_id")),
                                                                       limit=ROOM_CALL_SEGMENTS)})
        return {"messages": messages, "calls": calls}

    def _may_read(request: Request, subject: str, row: dict) -> Optional[str]:
        """None when this caller may read ``row``; else the refusal reason."""
        s = _store()
        if row.get("old_layout"):
            if row.get("owner") != subject:
                return "not_found"
        elif not s.can_read(row["id"], subject):
            return "not_found"
        if _incognito(subject, row):
            return "not_found"
        if identity_token.is_unwatched(request.headers) and row.get("owner") != subject:
            return "not_found"
        readers = _readers_of_here(request, subject)
        if readers is not None:
            if row.get("old_layout"):
                # only its owner has ever read it: anyone else in the chat asking makes it a leak
                if any(p != subject for p in readers):
                    return "shared_conversation"
            elif not all(s.can_read(row["id"], p) for p in readers):
                return "shared_conversation"
        return None

    def _audit(request: Request, subject: str, session: str, turns: int, verb: str) -> None:
        logger.info(json.dumps({"event": "chat_read", "verb": verb, "who": subject, "session": session,
                                "turns": turns, "from": identity_token.session_of(request.headers) or None}))

    @router.get("/api/chats/search")
    def chats_search(request: Request, query: Annotated[str, Query(max_length=200)] = "",
                     limit: Annotated[int, Query(ge=1, le=50)] = 10):
        """Find the person's chats by their words: titles and message text of the conversations the
        caller may read. Newest first; each hit has the session id, title, last activity and a snippet.
        An empty query lists the newest chats (what have I been talking about)."""
        subject = subject_of(request)
        s = _store()
        q = query.strip().lower()
        hits = []
        rows = sorted([*s.list_for(subject), *_old_rows(subject)],
                      key=lambda r: -float(r.get("last_active") or 0))[:SEARCH_MAX_SESSIONS]
        for row in rows:
            if _may_read(request, subject, row) is not None:
                continue
            title = str(row.get("title") or row.get("name") or "")
            snippet = ""
            if not q or q in title.lower():
                snippet = title
            if not snippet:
                try:
                    turns = _turns(subject, row)
                except Exception:  # noqa: BLE001 — an unreadable conversation is not a hit
                    continue
                for t in turns:
                    text = str(t.get("text") or "")
                    i = text.lower().find(q) if q else (0 if text.strip() else -1)
                    if i >= 0:
                        lo = max(0, i - SNIPPET // 2)
                        snippet = ("…" if lo else "") + text[lo:lo + SNIPPET]
                        break
            if snippet or not q:
                hits.append({"session": row["id"], "title": title, "last_active": row.get("last_active"),
                             "snippet": snippet})
                if len(hits) >= limit:
                    break
        _audit(request, subject, "*", len(hits), "search")
        return {"chats": hits}

    @router.get("/api/chats/{session}/turns")
    def chat_read(session: Annotated[str, Path(pattern=CHAT_REF_PATTERN)], request: Request,
                  since: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50):
        """One chat's turns — the person's words and the agent's replies — oldest first, from turn
        ``since`` (0-based), at most ``limit``; ``next`` is where the following page starts. Takes a
        session id or a `c:<session>` row from chat_folders_list."""
        subject = subject_of(request)
        s = _store()
        if session.startswith("c:"):
            session = session[2:]
        if not session:
            raise HTTPException(status_code=404, detail="chat not found")
        own = _own_room(request, session)
        if own is not None:
            turns = _turns(subject, own)
            page = turns[since:since + limit]
            _audit(request, subject, own["id"], len(page), "read-own-room")
            return {"session": own["id"], "title": own.get("title") or "", "turns": page,
                    "next": since + len(page) if since + len(page) < len(turns) else None,
                    **_room_extras(own)}
        # A ROOM READS ITS OWN HISTORY, AND ONLY THAT (int-s1, D3): the route is `shared: allow` so
        # a room's turn can call it at all; any other conversation stays refused to every shared
        # token and to a workspace principal, exactly as before.
        if identity_token.is_shared(request.headers) or identity_token.is_workspace_principal(request.headers):
            raise HTTPException(status_code=403, detail=identity_token.SHARED_REFUSAL)
        try:
            session = index.chat_for(subject, session) if index is not None else session
        except Exception:  # noqa: BLE001 — a meeting chat that cannot be mapped is read as named
            pass
        row = session_sync.resolve(s, subject, session, create=False)
        if row is None and not session.startswith(session_sync.STORE_ID_PREFIX):
            row = _old_row(subject, session)
        if row is None:
            raise HTTPException(status_code=404, detail="chat not found")
        why = _may_read(request, subject, row)
        if why == "shared_conversation":
            raise HTTPException(status_code=403, detail=identity_token.SHARED_REFUSAL)
        if why is not None:
            raise HTTPException(status_code=404, detail="chat not found")
        turns = _turns(subject, row)
        page = turns[since:since + limit]
        _audit(request, subject, row["id"], len(page), "read")
        return {"session": row["id"], "title": row.get("title") or "", "turns": page,
                "next": since + len(page) if since + len(page) < len(turns) else None}

    return router
