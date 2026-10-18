"""workspace_pins.py — pins in shared places are shared (UI decision 7ee; PRD 0.13.4 §6 "Pins").

Pinning a page, card, meeting or message in a workspace or a room pins it FOR EVERYONE there: it
shows in the room's "Pinned" strip and in the workspace README's ``{{pinned}}`` block, with who
pinned it and when. The store is the workspace's own ``.vexa/workspace.yaml`` (shared, travels with
the workspace) under ``pins`` (the workspace's) and ``room_pins.<session id>`` (one room's).

A pin is a REFERENCE (``[[kind:id]]`` text) and nothing else: no title, no excerpt, no value. Each
viewer's render resolves it through the one resolver with their own access, so a pin to something a
member cannot open renders "restricted" without a name (shot 91a), and a pin never grants (D18).
Private-chat pins are personal and are not stored here.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import yaml

from shared.gitexec import run_git
from workspaces.shared import links
from workspaces.shared import workspace_paths as wpaths

logger = logging.getLogger(__name__)

FILE = ".vexa/workspace.yaml"
PINS_MAX = 50
_ROOM_KEY = __import__("re").compile(r"^[\w.:-]{1,128}$")


class PinError(ValueError):
    """A pin that cannot be stored: not a reference, or too many."""


def _load(ws: Path) -> dict:
    raw = wpaths.read_text_inside(ws, FILE, max_bytes=200_000, allow=(".vexa",)) or ""
    try:
        doc = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError:
        logger.warning("workspace_pins: %s is not YAML; treated as empty", FILE)
        doc = {}
    return doc if isinstance(doc, dict) else {}


def _clean(items) -> list[dict]:
    out = []
    for p in items if isinstance(items, list) else []:
        if not isinstance(p, dict):
            continue
        ref = links.parse_universal(str(p.get("ref") or ""))
        if ref is None or ref.kind == "join":
            continue
        out.append({"ref": links.ref_chip(ref.kind, ref.id, anchor=ref.anchor), "by": str(p.get("by") or "")[:200],
                    "at": int(p.get("at") or 0)})
    return out[:PINS_MAX]


def read(ws: Path, room: Optional[str] = None) -> list[dict]:
    """The pins of the workspace (``room=None``) or of one room in it, newest first."""
    doc = _load(Path(ws))
    items = (doc.get("room_pins") or {}).get(room) if room else doc.get("pins")
    return sorted(_clean(items), key=lambda p: -p["at"])


def _commit(ws: Path, message: str) -> None:
    if not (ws / ".git").exists():
        return
    try:
        run_git(ws, "add", "--", FILE, check=True)
        run_git(ws, "-c", "user.name=vexa-terminal", "-c", "user.email=terminal@vexa.local",
                "commit", "-q", "-m", message, "--", FILE)
    except Exception:  # noqa: BLE001 — the file is authoritative; a later commit picks it up
        logger.warning("workspace_pins: commit failed in %s", ws)


def set_pin(ws: Path, ref_text: str, *, by: str, pinned: bool, room: Optional[str] = None,
            now: Optional[float] = None) -> list[dict]:
    """Pin (or unpin) one reference for everyone in the workspace or the room. Idempotent."""
    ws = Path(ws)
    ref = links.parse_universal(ref_text)
    if ref is None or ref.kind == "join":
        raise PinError("not a reference: pin a [[kind:id]] chip or a /r/<kind>/<id> link")
    if room is not None and not _ROOM_KEY.fullmatch(room):
        raise PinError("not a room")
    chip = links.ref_chip(ref.kind, ref.id, anchor=ref.anchor)
    doc = _load(ws)
    if room:
        rooms = doc.get("room_pins") if isinstance(doc.get("room_pins"), dict) else {}
        items = _clean(rooms.get(room))
    else:
        items = _clean(doc.get("pins"))
    items = [p for p in items if p["ref"] != chip]
    if pinned:
        if len(items) >= PINS_MAX:
            raise PinError(f"at most {PINS_MAX} pins; unpin one first")
        items.append({"ref": chip, "by": str(by)[:200], "at": int(now if now is not None else time.time())})
    if room:
        rooms[room] = items
        doc["room_pins"] = rooms
    else:
        doc["pins"] = items
    wpaths.write_text_inside(ws, FILE, yaml.safe_dump(doc, sort_keys=True, allow_unicode=True), allow=(".vexa",))
    _commit(ws, f"{'pin' if pinned else 'unpin'} {chip}" + (f" in room {room}" if room else ""))
    return sorted(items, key=lambda p: -p["at"])
