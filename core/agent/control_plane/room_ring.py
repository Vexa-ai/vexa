"""room_ring.py — RINGING A ROOM'S MEMBERS (0.13.4 S4; sharing-tagging §2, §7; D36).

A call started in a room rings the room's members — and nobody else: a workspace member who is not
in the room sees the live call on the workspace page (7l), which is discovery, not an invitation.
Who counts is ``room_membership.ringable`` (a closed room: every member; an open room: the derived
members who opened it within the tuning window). A member added while the call is live is rung at
once.

A ring is two things, both through the notifier (agent-api the one writer):
* a live ``ring`` frame on the person's own channel — the app rings, with the call's room and who
  started it (ids only, never a message);
* a ``call_started`` notice that EXPIRES: worth showing only while the call can still be live.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

from control_plane import room_membership as rm

log = logging.getLogger("agent_api.room_ring")

#: How long the ring's notice is shown. A call nobody answered for this long is not a ring any more.
RING_NOTICE_TTL_S = 30 * 60


def ring(store, root: Path, notifier, session_id: str, *, caller: str, caller_name: str = "",
         title: str = "", only: Optional[Iterable[str]] = None, call_room: str = "") -> list[str]:
    """Ring the room's ringable members (or just ``only``, each still checked against the room) and
    return who was rung. Never raises: the call or the add already happened."""
    try:
        who = rm.ringable(store, root, session_id, exclude=caller)
        if only is not None:
            allowed = set(str(x) for x in only if x and str(x) != str(caller))
            # a person added mid-call is rung whether or not they opened the room before
            members = set(rm.humans(store, root, session_id))
            who = [x for x in allowed if x in members]
        if not who or notifier is None:
            return list(who)
        notifier.emit(who, kind="call_started", actor=str(caller), actor_name=caller_name,
                      object_kind="chat", object_id=session_id, object_name=title or "a room",
                      expires_in=RING_NOTICE_TTL_S)
        for person in who:
            notifier.send_frame(person, {"type": "ring", "session": session_id, "by": str(caller),
                                         **({"room": call_room} if call_room else {})})
        return list(who)
    except Exception as exc:  # noqa: BLE001 — a ring is the extra; the act stands
        log.warning("ring not sent for %s (%s)", session_id, type(exc).__name__)
        return []


__all__ = ["RING_NOTICE_TTL_S", "ring"]
