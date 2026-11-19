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

A RING IS RATE-LIMITED, here, where every ring is made (``ring``): one caller rings one person at
most ``VEXA_ROOM_RING_PER_PERSON`` times, and anyone at most ``VEXA_ROOM_RING_PER_CALLER`` times,
in any ``RING_RATE_WINDOW_S``. A person over the limit is not rung (and not in ``rang``). A route
that exists to ring (``call_person``) asks ``check`` first and answers 429 ``ring_rate_limited``
with ``Retry-After`` before it starts anything. The count lives in this process: agent-api is one
replica, and a restart forgives at most one window.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

from control_plane import room_membership as rm

log = logging.getLogger("agent_api.room_ring")

#: How long the ring's notice is shown. A call nobody answered for this long is not a ring any more.
RING_NOTICE_TTL_S = 30 * 60


#: The window both ring limits count over.
RING_RATE_WINDOW_S = 600
PER_PERSON_ENV, PER_PERSON_DEFAULT = "VEXA_ROOM_RING_PER_PERSON", 3
PER_CALLER_ENV, PER_CALLER_DEFAULT = "VEXA_ROOM_RING_PER_CALLER", 30
REFUSED = "ring_rate_limited"

_lock = threading.Lock()
_sent: dict[tuple, deque] = {}       # (caller,) and (caller, person) -> times of rings sent


def _limit(env: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(env, default)))
    except ValueError:
        return default


class RingLimited(Exception):
    """Every person this ring would reach is over the caller's limit. ``retry_after`` in seconds."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"ring rate limit; retry after {retry_after:.0f}s")
        self.retry_after = max(1, int(retry_after + 0.999))

    def body(self) -> dict:
        return {"refused": REFUSED, "retry_after": self.retry_after,
                "said": (f"They were rung too many times just now. Nobody was rung again; it can ring "
                         f"them in {self.retry_after} s.")}


def reset() -> None:
    """Forget every count (tests)."""
    with _lock:
        _sent.clear()


def _window(key: tuple, now: float) -> deque:
    q = _sent.setdefault(key, deque())
    while q and now - q[0] >= RING_RATE_WINDOW_S:
        q.popleft()
    return q


def _wait(caller: str, person: str, now: float) -> float:
    """0 when ``caller`` may ring ``person`` now; else the seconds until they may."""
    waits = []
    for key, cap in (((caller,), _limit(PER_CALLER_ENV, PER_CALLER_DEFAULT)),
                     ((caller, person), _limit(PER_PERSON_ENV, PER_PERSON_DEFAULT))):
        q = _window(key, now)
        if len(q) >= cap:
            waits.append(RING_RATE_WINDOW_S - (now - q[len(q) - cap]))
    return max(waits) if waits else 0.0


def check(caller: str, people: Iterable[str], *, now: Optional[float] = None) -> None:
    """Raise ``RingLimited`` when NONE of ``people`` may be rung by ``caller`` now (nobody to ring is
    no refusal). Counts nothing: ``ring`` counts what it sends."""
    now = time.time() if now is None else now
    people = [str(p) for p in people if p and str(p) != str(caller)]
    if not people:
        return
    with _lock:
        waits = [_wait(str(caller), p, now) for p in people]
    if all(w > 0 for w in waits):
        raise RingLimited(min(waits))


def _admit(caller: str, people: list, now: float) -> list:
    """The people ``caller`` may ring now, each counted as rung."""
    out = []
    with _lock:
        for p in people:
            if _wait(caller, str(p), now) > 0:
                continue
            _window((caller,), now).append(now)
            _window((caller, str(p)), now).append(now)
            out.append(p)
    return out


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
        limited = set(who)
        who = _admit(str(caller), list(who), time.time())
        if limited - set(who):
            log.info("ring from %s in %s: %d over the ring limit, not rung", caller, session_id,
                     len(limited - set(who)))
        if not who:
            return []
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


__all__ = ["RING_NOTICE_TTL_S", "RING_RATE_WINDOW_S", "RingLimited", "check", "reset", "ring"]
