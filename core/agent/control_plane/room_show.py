"""WHAT A CHAT IS SHOWING (founder 2026-10-15; JTBD-1 2026-10-16: a room whose call is on Meet).

One page per session, shown by one member — the presenter — to everyone in it. In an in-app call it
also travels over the call's data channel; in a room without one, every member's app reads it here
through the room's `messages` read and opens it in its panel. The presenter keeps control: only they
replace or stop it. EPHEMERAL: this process only, never stored; the chat keeps one line per show.
"""
from __future__ import annotations

import itertools
import threading
from typing import Optional

_lock = threading.Lock()
_seq = itertools.count(1)
_showing: dict[str, dict] = {}


def current(session_id: str) -> Optional[dict]:
    with _lock:
        s = _showing.get(session_id)
        return dict(s) if s else None


def show(session_id: str, *, by: str, workspace: str, path: str, title: str) -> dict:
    with _lock:
        rec = {"by": by, "workspace": workspace, "path": path, "title": title, "seq": next(_seq)}
        _showing[session_id] = rec
        return dict(rec)


def stop(session_id: str) -> None:
    with _lock:
        _showing.pop(session_id, None)
