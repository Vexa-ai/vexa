"""rooms.py — ROOMS are created in a workspace; chats are always private (founder 2026-10-15).

> "normal chat becomes shared chat when people join and need cheap sensor to know when agent
> needs to react" — then, decided: chats stay personal, rooms are separate.

All of it is behind the ``rooms`` feature, OFF by default: off, nothing here runs and shared chats
are exactly what they were.

A CHAT IS PRIVATE: you and the agent. With rooms on, no one is added to a chat. A ROOM is made in a
workspace (`POST /api/rooms`), OPEN by default — listed to that workspace's members, who join it
themselves — or CLOSED (invisible to anyone not in it; its members invite). A person's message in a
room is appended to the room's log and published to the room's stream (`room:{session}:msgs`,
agent-api the one writer) for the `signals` service's chat source — it does not start a turn, unless
it is rule 1: `@vexa`, the agent's name, a reply to the agent's message (`session_access.mentioned`,
`ChatBody.reply_to`), which starts a normal, trusted turn at once.

A ROOM HAS A ROW (`agent_session_room`): visibility (open | closed | direct), proactivity (off |
quiet | active — any member changes it, and the change is said) and the listening brief the agent
writes for the room's sensor. The agent never reads room B from room A unless everybody reading A's
answer is in B (`routers/chat_read.py`, the leak rule) — so an open room never quotes a closed one.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger("agent_api.rooms")

DEFAULT_PROACTIVITY = "quiet"
#: How many messages a room's stream keeps (approximate trim): the sensor reads it as it arrives.
STREAM_MAXLEN = 5000
AGENT_AUTHOR = "vexa"


FEATURE = "rooms"


def enabled(global_root=None, subject: Optional[str] = None) -> bool:
    """Is room mode switched on — for the company, or for ``subject`` by name
    (`features_accounts`, the test-account switch)? Default off. Off, a shared chat behaves exactly
    as it did before rooms: no line, no stream, no room wake."""
    from control_plane import features  # noqa: PLC0415
    try:
        root = global_root() if callable(global_root) else global_root
    except Exception:  # noqa: BLE001
        root = None
    try:
        return bool(features.effective(root, subject=subject or None).get(FEATURE))
    except Exception:  # noqa: BLE001 — a switch that cannot be read is off
        return False


def enabled_for_anyone(global_root=None) -> bool:
    from control_plane import features  # noqa: PLC0415
    try:
        root = global_root() if callable(global_root) else global_root
        return features.on_for_anyone(root, FEATURE)
    except Exception:  # noqa: BLE001
        return False


def stream_key(session_id: str) -> str:
    """The room's message stream — agent-api is its one writer, the `signals` chat source reads it."""
    return f"room:{session_id}:msgs"


def humans(store, session_id: str) -> int:
    try:
        return len(store.humans(session_id))
    except Exception:  # noqa: BLE001 — a store that cannot answer is not a room
        return 0


def is_room(store, session_id: str) -> bool:
    """A room is a session made as one (it has a room row); a chat never becomes one."""
    try:
        return store.room(session_id) is not None
    except Exception:  # noqa: BLE001 — a store that cannot answer is not a room
        return False


def settings(store, session_id: str) -> dict:
    """What a room is, as a reader is shown it — also for a private chat (``room: False``)."""
    row = store.room(session_id) if store is not None else None
    n = humans(store, session_id)
    return {"room": row is not None, "humans": n,
            "visibility": (row or {}).get("visibility") or "private",
            "proactivity": (row or {}).get("proactivity") or DEFAULT_PROACTIVITY,
            "workspace": (row or {}).get("workspace") or "",
            "listen": (row or {}).get("listen") or {},
            "listen_version": int((row or {}).get("listen_version") or 0),
            # ROOM-LEVEL SETTINGS (R1): one level and one write target for everybody in the room —
            # never the per-person chat record. The write target is the room's workspace, fixed.
            "level": (row or {}).get("level") or "",
            "write_target": (row or {}).get("workspace") or "",
            "created_by": (row or {}).get("created_by") or "",
            "cross_org": bool((row or {}).get("cross_org"))}


_CLIENTS: dict = {}


def redis_for(redis_url: Optional[str]):
    """The service redis the room streams are written to (one client per url), or None."""
    if not redis_url:
        return None
    if redis_url not in _CLIENTS:
        try:
            import redis  # noqa: PLC0415

            _CLIENTS[redis_url] = redis.from_url(redis_url, decode_responses=True)
        except Exception:  # noqa: BLE001
            return None
    return _CLIENTS[redis_url]


def entry(row: dict, note: dict, *, author_kind: str = "person", author_name: str = "",
          agent_name: str = "Vexa", reply_to: Optional[dict] = None) -> dict:
    """One message as the room stream carries it — the shape the signals chat source reads
    (one ``payload`` field, JSON): who wrote it, what, when, and the room's owner as ``subject``."""
    payload = {"message_id": f"n-{note.get('seq', '')}", "session": row["id"],
               "subject": row.get("owner") or "",
               "author": {"kind": author_kind, "id": str(note.get("author") or ""),
                          "name": author_name or str(note.get("author") or "")},
               "text": str(note.get("text") or ""), "at": float(note.get("created") or time.time()),
               "agent_name": agent_name}
    if reply_to:
        payload["reply_to"] = reply_to
    return {"payload": json.dumps(payload, ensure_ascii=False)}


def publish(r, row: dict, note: dict, *, author_name: str = "", author_kind: str = "person") -> Optional[str]:
    """Put one room message on the room's stream for the sensor; the stream id, or None when no
    redis answers (the message is in the chat's log either way)."""
    if r is None or not note or not row:
        return None
    if author_kind == "agent":
        try:
            r.delete(heard_key(row["id"]))           # the reply replaces "Vexa heard you · thinking…"
        except Exception:  # noqa: BLE001
            pass
    try:
        return r.xadd(stream_key(row["id"]), entry(row, note, author_kind=author_kind,
                                                   author_name=author_name),
                      maxlen=STREAM_MAXLEN, approximate=True)
    except Exception as exc:  # noqa: BLE001 — the sensor misses one line; the log has it
        logger.warning("room message not published for %s (%s)", row.get("id"), type(exc).__name__)
        return None


# ── "VEXA HEARD YOU · THINKING…" (founder, app.dev 2026-10-16) ─────────────────────────────────────
# The room shows AT ONCE that Vexa is on it: the moment a sensor's wake is accepted for the room (not
# when its turn starts), until the reply replaces it — or, when the turn ends saying nothing, a brief
# "Vexa: nothing to add". agent-api writes it (the wake door) and clears it (the room's agent's line).

HEARD_TTL_S = 600
#: a wake accepted this recently is "thinking" even before its turn holds the room (dispatch, queue)
HEARD_GRACE_S = 30.0
#: how long "nothing to add" is reported once the turn ended without a reply (the client shows 3 s;
#: this outlasts one poll of the room)
NOTHING_S = 8.0


def current_step(r, session_id: str) -> Optional[dict]:
    """The step the turn holding the room is on right now — ``{tool, file_path?}`` from its output
    stream (the latest tool call since its last finished turn) — or None. Only the tool and the page
    it touches: never the call's other arguments."""
    if r is None:
        return None
    try:
        from control_plane import session_sync  # noqa: PLC0415
        from shared import units  # noqa: PLC0415
        holder = r.get(session_sync.lease_key(session_id))
        if not holder:
            return None
        rows = r.xrevrange(units.output_topic(str(holder)), count=40)
    except Exception:  # noqa: BLE001
        return None
    for _id, fields in rows or []:
        try:
            ev = json.loads((fields or {}).get("event") or "{}")
        except (TypeError, ValueError):
            continue
        kind = ev.get("type") if isinstance(ev, dict) else None
        if kind == "turn-complete":
            return None
        if kind == "tool-call" and ev.get("tool"):
            out = {"tool": str(ev["tool"])[:120]}
            fp = (ev.get("args") or {}).get("file_path") if isinstance(ev.get("args"), dict) else None
            if isinstance(fp, str) and fp:
                out["file_path"] = fp[:300]
            return out
    return None


def heard_key(session_id: str) -> str:
    return f"room:{session_id}:heard"


def note_heard(r, session_id: str, reason: str, *, now: Optional[float] = None) -> None:
    if r is None or not session_id:
        return
    try:
        r.set(heard_key(session_id), json.dumps({"at": float(now if now is not None else time.time()),
                                                 "reason": str(reason or "")}), ex=HEARD_TTL_S)
    except Exception:  # noqa: BLE001 — the status is a courtesy
        pass


def turn_status(r, session_id: str, *, busy: bool, now: Optional[float] = None) -> Optional[str]:
    """``"thinking"`` · ``"nothing"`` · None, for the room's turn-status line."""
    if r is None:
        return None
    now = float(now if now is not None else time.time())
    try:
        raw = r.get(heard_key(session_id))
        h = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(h, dict):
        return None
    ended = h.get("ended_at")
    if ended is not None:
        return "nothing" if now - float(ended) < NOTHING_S else None
    if busy or now - float(h.get("at") or 0) < HEARD_GRACE_S:
        return "thinking"
    # the turn ended and said nothing (a reply would have cleared the key)
    try:
        r.set(heard_key(session_id), json.dumps({**h, "ended_at": now}), ex=int(NOTHING_S) + 2)
    except Exception:  # noqa: BLE001
        pass
    return "nothing"


def agent_busy(r, session_id: str) -> bool:
    """IS THE ROOM'S AGENT ACTING RIGHT NOW? (the room header's "Vexa is checking…") — positive
    evidence only: one of the room's own agent units holds the conversation's head lease."""
    if r is None:
        return False
    try:
        from control_plane import room_principal, session_sync  # noqa: PLC0415
        holder = r.get(session_sync.lease_key(session_id)) or ""
    except Exception:  # noqa: BLE001 — unknown is not busy
        return False
    return room_principal.is_room_unit(str(holder))


#: WHO THE ROOM'S AGENT IS ACTING FOR RIGHT NOW (1.6). A room runs on one warm worker whatever
#: the speaker, so the worker's spawn-time env names only the first one; agent-api records each
#: dispatched turn's speaker here and reads it back when that turn writes a page during a call.
#: One turn per room at a time (the head lease), so one key per room is the whole answer.
SPEAKER_TTL_S = 6 * 3600


def speaker_key(session_id: str) -> str:
    return f"room:{session_id}:speaker"


def note_speaker(r, session_id: str, who: str) -> None:
    if r is None or not session_id or not who:
        return
    try:
        r.set(speaker_key(session_id), str(who), ex=SPEAKER_TTL_S)
    except Exception:  # noqa: BLE001 — the turn outranks its attribution
        pass


def speaker_of(r, session_id: str) -> str:
    if r is None or not session_id:
        return ""
    try:
        return str(r.get(speaker_key(session_id)) or "")
    except Exception:  # noqa: BLE001 — unknown is unattributed, never guessed
        return ""


#: WHAT EACH PROACTIVITY MEANS to the room's sensor (founder model: off · quiet · active). The brief
#: the signals service reads is the room's brief with this applied — the agent tunes the brief, the
#: people in the room choose how readily it speaks up.
ACTIVE_WAKE_FACTOR = 0.5
ACTIVE_MAX_WAIT_S = 120


def effective_brief(brief: dict, proactivity: str) -> dict:
    """``brief`` as the sensor runs it: ``quiet`` as written; ``active`` wakes on half the
    information and waits at most ``ACTIVE_MAX_WAIT_S``; ``off`` wakes only when addressed."""
    from control_plane import meeting_listen  # noqa: PLC0415
    b = dict(brief or {})
    if proactivity == "active":
        lo = meeting_listen.BOUNDS["wake_at"][0]
        b["wake_at"] = max(lo, round(float(b.get("wake_at") or meeting_listen.DEFAULT_BRIEF["wake_at"])
                                     * ACTIVE_WAKE_FACTOR, 3))
        b["max_wait_s"] = min(int(b.get("max_wait_s") or meeting_listen.DEFAULT_BRIEF["max_wait_s"]),
                              ACTIVE_MAX_WAIT_S)
    elif proactivity == "off":
        b["labels"] = [{**lab, "wake": lab.get("name") == meeting_listen.ADDRESSED}
                       for lab in (b.get("labels") or [])]
    return b


def can_join(store, session_id: str, who: str, *, is_member: Callable[[str, str], bool]) -> bool:
    """An OPEN room's self-join: the person is a member of the room's workspace."""
    row = store.room(session_id)
    if not row or row["visibility"] != "open" or not row["workspace"]:
        return False
    try:
        return bool(is_member(row["workspace"], who))
    except Exception:  # noqa: BLE001 — an unreadable member list is not a membership
        return False


def visible_to(store, session_id: str, who: str, *, is_member: Callable[[str, str], bool]) -> bool:
    """Does ``who`` see this room exists? Its members always; an open room's workspace members too.
    A closed or direct room is invisible to everyone else."""
    if store.can_read(session_id, who):
        return True
    return can_join(store, session_id, who, is_member=is_member)


def brief_json(listen: dict) -> str:
    return json.dumps(listen or {}, sort_keys=True)
