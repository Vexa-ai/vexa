"""display_name.py — a meeting's name for a person to read, one rule for the agent, the home, the
rail and notifications (UI decisions 14; founder 2026-10-15, on "vexa_call · call-21ca0ef92…":
"humans get crazy with these names").

In order: the meeting's own title (a calendar event's, or one a person gave it) · the name of the
chat it was started in · the people in it ("Call with Valeriia", "Call with Valeriia and Ana") ·
"Call with Vexa" for a call nobody else is in. Never a native id, a session id or a platform key —
the platform is an icon in the client, and the native id is only in "…" → Details. The terminal's
`minutes/callsList.ts` `meetingDisplayName` is the same rule.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

_RAW = [
    re.compile(r"\b(?:google_meet|vexa_call|voice_note|ms_teams)\b", re.I),
    re.compile(r"\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b"),
    re.compile(r"\b(?:call|vn|pchat|scaffold|meet|sess|session|unit|agent|flow|dm)-[0-9a-z][\w-]{4,}\b", re.I),
    re.compile(r"\bs-[0-9a-f]{8,}\b", re.I),
    re.compile(r"(?:^|\s)\d{9,12}(?:\s|$)"),
    re.compile(r"(?:^|\s)#\d+$"),
]
_PLACEHOLDER = re.compile(r"^(google meet|zoom|microsoft teams|teams|jitsi|live|voice note|untitled|vexa)?\s*(meeting|call)?$", re.I)
_NOBODY = re.compile(r"^(speaker\s*[a-z]?\d*|unknown( speaker)?|unidentified( speaker)?|guest|participant\s*\d*|vexa( bot)?|.*notetaker|.*\bbot)$", re.I)


def looks_raw(text) -> bool:
    """An id, a platform key or a placeholder — not a name."""
    t = str(text or "").strip()
    return not t or bool(_PLACEHOLDER.match(t)) or any(r.search(t) for r in _RAW)


def _people(row: dict, me: Iterable[str]) -> tuple:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    raw = row.get("participants") or data.get("participants") or data.get("attendees") or []
    mine = {str(m).strip().lower() for m in me if m}
    out, seen = [], set()
    for p in raw if isinstance(raw, list) else []:
        name = str(p.get("name") if isinstance(p, dict) else p or "").strip()
        if not name or _NOBODY.match(name) or looks_raw(name) or name.lower() in seen:
            continue
        if name.lower() in mine or name.split()[0].lower() in {m.split("@")[0].split(".")[0] for m in mine}:
            continue
        seen.add(name.lower())
        out.append(name)
    return out, len(raw) if isinstance(raw, list) else 0


def meeting_display_name(row: dict, chat_name: str = "", me: Optional[Iterable[str]] = None) -> str:
    """The name a person reads for this meeting row (meeting-api's shape: ``title``, ``platform``,
    ``data.title``, ``data.participants``)."""
    row = row or {}
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    for t in (row.get("title_custom"), data.get("title"), row.get("title"), chat_name):
        if t and not looks_raw(t):
            return str(t).strip()[:80]
    platform = str(row.get("platform") or "")
    if platform in ("voice_note", "voice-note"):
        return "Voice note"
    people, count = _people(row, me or ())
    if platform == "vexa_call" and count <= 1:
        return "Call with Vexa"
    if not people:
        return "Call with Vexa"
    if len(people) == 1:
        return f"Call with {people[0]}"
    if len(people) == 2:
        return f"Call with {people[0]} and {people[1]}"
    return f"Call with {people[0]}, {people[1]} and {len(people) - 2} more"


def readable_object(name, kind: str = "meeting") -> str:
    """A notification's object as words: a name stays, an id becomes "your call" / "your meeting"."""
    n = str(name or "").strip()
    if n and not looks_raw(n):
        return n
    return "your call" if re.search(r"\bcall-|vexa_call", n, re.I) else f"your {kind or 'meeting'}"
