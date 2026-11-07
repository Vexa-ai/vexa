"""room_switches.py — "VEXA IN THIS ROOM" AND "TRANSCRIBE CALLS" (UI 7hh, PRD 0.13.4 S4, deny test 25).

Vexa is present in every room, rooms of two included, unless switched off. Two independent switches:

* **Vexa in this room** (``vexa``) — off: no agent turn, no signal wake, no card proposed from the room
  (enforced where each starts: ``session_turn.plan_turn``, the ``room.signal`` door, the listen brief);
* **Transcribe calls** (``transcribe``) — off: a call here binds untranscribed (no transcript, no notes).

WHO SWITCHES (both are default policy lines, R12; a workspace DENY line on
``room.switch.<name>.<on|off>`` LOCKS one, and the refusal names that rule):
* a ROOM OF TWO: either person turns either switch OFF alone; turning it back ON needs both — the
  first "on" is an ask, delivered to the other person as a card, and only their accept turns it on;
* a BIGGER ROOM: its owners switch (the room's owner, or an owner of its workspace); members see the
  state and the lock.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from control_plane import pair_rooms
from control_plane import policy_decide as pd
from control_plane import workspace_membership as m

SWITCHES = {"vexa": "agent_on", "transcribe": "transcribe"}
WORDS = {"vexa": "Vexa", "transcribe": "call transcription"}


class SwitchRefused(Exception):
    def __init__(self, status: int, refused: str, said: str, **extra) -> None:
        super().__init__(said)
        self.status, self.detail = status, {"refused": refused, "said": said, **extra}


def state(room: dict) -> dict:
    return {"vexa": room.get("agent_on", True) is not False,
            "transcribe": room.get("transcribe", True) is not False,
            "asks": room.get("switch_asks") or {}}


def lock_of(root: Path, workspace: str, who: str, switch: str, on: bool, *, credential: str) -> Optional[str]:
    """The rule that locks this change, or None. A rule set that cannot be read refuses (FD3)."""
    out = pd.decide(pd.Ask(principal=str(who), action=f"room.switch.{switch}.{'on' if on else 'off'}",
                           workspace=workspace, credential=credential), root=root)
    if out.decision == pd.REFUSE:
        raise SwitchRefused(503, pd.POLICY_UNREADABLE, "The room's policy cannot be read, so nothing changed.",
                            rule=out.rule)
    return None if out.allowed else out.rule


def locks(root: Path, workspace: str, who: str, *, credential: str = pd.SESSION) -> dict:
    """For the ⋯ menu: each switch's lock in each direction, ``{switch: {on: rule|None, off: rule|None}}``."""
    out = {}
    for sw in SWITCHES:
        try:
            out[sw] = {d: lock_of(root, workspace, who, sw, d == "on", credential=credential) for d in ("on", "off")}
        except SwitchRefused:
            out[sw] = {"on": pd.POLICY_UNREADABLE, "off": pd.POLICY_UNREADABLE}
    return out


def _is_owner(store, root: Path, session_id: str, room: dict, who: str) -> bool:
    if store.role_of(session_id, who) == "owner":
        return True
    try:
        return bool(room.get("workspace")) and m.is_member(root, room["workspace"], who) == "owner"
    except m.MembershipError:
        return False


def change(store, root: Path, session_id: str, who: str, switch: str, on: bool, *, credential: str,
           now: Optional[float] = None) -> dict:
    """Set ``switch`` to ``on`` for ``who``. Returns ``{applied, asked?, state}``; raises SwitchRefused."""
    if switch not in SWITCHES:
        raise SwitchRefused(422, "not_a_switch", "The switches are vexa and transcribe.")
    room = store.room(session_id)
    if room is None:
        raise SwitchRefused(409, "not_a_room", "A private chat has no room switches.")
    if store.role_of(session_id, who) not in ("owner", "member"):
        raise SwitchRefused(403, "not_a_member", "Only the room's members change its switches.")
    cur = state(room)
    rule = lock_of(root, room.get("workspace") or "", who, switch, on, credential=credential)
    if rule:
        raise SwitchRefused(423, "locked_by_policy",
                            f"{WORDS[switch].capitalize()} is locked {'off' if on else 'on'} here by policy.", rule=rule)
    if cur[switch] == on and not (on and cur["asks"].get(switch)):
        return {"applied": False, "state": cur}
    asks = dict(cur["asks"])
    pair = pair_rooms.is_pair_room(store, root, session_id)
    if pair:
        if not on:
            asks.pop(switch, None)
            st = store.put_switches(session_id, asks=asks, **{SWITCHES[switch]: False})
            return {"applied": True, "state": state(st)}
        ask = asks.get(switch)
        if ask and str(ask.get("by")) != str(who):
            # the other person already asked: this is the second consent
            asks.pop(switch, None)
            st = store.put_switches(session_id, asks=asks, **{SWITCHES[switch]: True})
            return {"applied": True, "state": state(st)}
        asks[switch] = {"by": str(who), "at": float(now if now is not None else time.time())}
        st = store.put_switches(session_id, asks=asks)
        other = next((p for p in pair_rooms.people(root, room["workspace"]) if p != str(who)), "")
        return {"applied": False, "asked": other, "state": state(st)}
    if not _is_owner(store, root, session_id, room, who):
        raise SwitchRefused(403, "owners_switch", "In this room its owners change the switches.")
    asks.pop(switch, None)
    st = store.put_switches(session_id, asks=asks, **{SWITCHES[switch]: on})
    return {"applied": True, "state": state(st)}


def answer(store, root: Path, session_id: str, who: str, switch: str, accept: bool) -> dict:
    """The other person answers a turn-on ask (the card): accept turns it on, decline keeps it off."""
    room = store.room(session_id)
    if room is None or switch not in SWITCHES:
        raise SwitchRefused(404, "no_ask", "Nothing is waiting for an answer here.")
    ask = (state(room)["asks"] or {}).get(switch)
    if not ask or store.role_of(session_id, who) not in ("owner", "member"):
        raise SwitchRefused(404, "no_ask", "Nothing is waiting for an answer here.")
    if str(ask.get("by")) == str(who):
        raise SwitchRefused(409, "your_own_ask", "The other person answers your ask.")
    asks = dict(state(room)["asks"])
    asks.pop(switch, None)
    st = store.put_switches(session_id, asks=asks, **({SWITCHES[switch]: True} if accept else {}))
    return {"applied": bool(accept), "state": state(st)}


def agent_on(store, session_id: str) -> bool:
    """Is Vexa switched on in this session? A chat (no room row) always is."""
    try:
        room = store.room(session_id)
    except Exception:  # noqa: BLE001 — a store that cannot answer: not a room the switch applies to
        return True
    return room is None or room.get("agent_on", True) is not False


def transcribe_on(store, session_id: str) -> bool:
    try:
        room = store.room(session_id)
    except Exception:  # noqa: BLE001
        return True
    return room is None or room.get("transcribe", True) is not False


__all__ = ["SWITCHES", "SwitchRefused", "agent_on", "answer", "change", "lock_of", "locks", "state",
           "transcribe_on"]
