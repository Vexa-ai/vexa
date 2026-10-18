"""room_echo.py — what the room's agent said, onto the room's stream (rooms, R2 1.3).

The signals service's chat source reads `room:{session}:msgs` (agent-api its one writer). People's
messages reach it when they are posted (`rooms.publish`). The AGENT's own messages must reach it too —
never to be scored, only as context, and because "a question right after the agent spoke" is one of
the sensor's rules. A room's turns run on its workspace agent's units
(`agent-ws-<wid>-room-<session>[-signal|-auto]`), whose output streams this reads: the text of each
finished turn becomes one entry, `author.kind: agent`.

ONE WRITER STILL: this runs inside agent-api, the stream's one writer. Its read position per unit is
kept beside the stream (`room:{session}:echo:{unit}`), so a restart neither repeats nor drops a turn.
A turn that said nothing writes nothing. Off with the `rooms` feature.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Callable, Optional

from control_plane import rooms
from shared.units import output_topic

logger = logging.getLogger("agent_api.room_echo")

_ROOM_UNIT = re.compile(r"^agent-ws-.+?-room-(?P<session>[A-Za-z0-9._-]+?)(?:-read)?(?:-signal|-auto)?$")
MAX_TEXT = 4000
POLL_S = 2.0


def session_of(unit_id: str) -> str:
    m = _ROOM_UNIT.match(str(unit_id or ""))
    return m.group("session") if m else ""


#: One look reads at most this many entries (never a whole stream); the rest is read next look.
ECHO_SCAN_MAX = 2000


def cursor_key(session_id: str, unit_id: str) -> str:
    return f"room:{session_id}:echo:{unit_id}"


def echo_unit(r, store, unit_id: str, *, agent_name: str = "Vexa") -> int:
    """Read ``unit_id``'s output since the last look and publish each finished turn's text to its
    room's stream. Returns how many messages were published."""
    sid = session_of(unit_id)
    if not sid:
        return 0
    row = store.get(sid) if store is not None else None
    if row is None:
        return 0
    key = cursor_key(sid, unit_id)
    start = r.get(key) or "0-0"
    entries = r.xrange(output_topic(unit_id), min=f"({start}" if start != "0-0" else "-", max="+",
                       count=ECHO_SCAN_MAX)
    if not entries:
        return 0
    text, published, last_done = [], 0, None
    for eid, fields in entries:
        try:
            ev = json.loads(fields.get("event") or "{}")
        except ValueError:
            ev = {}
        t = ev.get("type")
        if t == "message-delta" and ev.get("text"):
            text.append(str(ev["text"]))
        elif t == "turn-complete":
            said = "".join(text).strip()
            text = []
            last_done = eid
            if said:
                note = {"seq": f"a-{eid}", "author": "vexa", "text": said[:MAX_TEXT], "created": time.time()}
                if rooms.publish(r, row, note, author_name=agent_name, author_kind="agent"):
                    published += 1
    if last_done is not None:
        r.set(key, last_done)            # an unfinished turn is read again, whole, next time
    return published


def room_units(r) -> list:
    """The room units with an output stream right now."""
    out = []
    for k in r.scan_iter(match="unit:agent-ws-*-room-*:out", count=500):
        k = k.decode() if isinstance(k, bytes) else str(k)
        out.append(k[len("unit:"):-len(":out")])
    return out


def start(redis_factory: Callable[[], object], store, *, enabled: Callable[[], bool],
          poll_s: float = POLL_S) -> Optional[threading.Thread]:
    """The echo loop, a daemon thread; does nothing while the `rooms` feature is off. Never raises."""
    def loop():
        while True:
            try:
                if enabled():
                    r = redis_factory()
                    if r is not None:
                        for unit in room_units(r):
                            echo_unit(r, store, unit)
            except Exception as exc:  # noqa: BLE001 — one missed poll is a message late, never a crash
                logger.warning("room echo poll failed (%s)", type(exc).__name__)
            time.sleep(poll_s)
    t = threading.Thread(target=loop, daemon=True, name="room-echo")
    t.start()
    return t
