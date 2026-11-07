"""room_plan.py — a ROOM's planned call, on its meeting row (rooms, decision F1, R1 1.4).

A planned call is a planned meeting (`POST /meetings`, an intent status, no bot) that a room owns:
`data.room` (the room's session id), `data.agenda` (its agenda page) and `data.calendar_event_ref`
(the creator's calendar event — the source of truth for its time and invitees, written back by the
calendar sensor). meeting-api owns the row; the room only names itself on it.

Switched on with the `rooms` feature, BY NAME ONLY: `VEXA_FEATURES=all` does not include it (the
same rule as agent-api's `explicit` features). Off, a body naming any of these keys is refused with
the one `feature_disabled` body, and every other planned meeting is exactly what it was.

`ics(row)` renders the row as an iCalendar event (RFC 5545) for a creator with no connected calendar:
the creator downloads it and sends it themselves — this service never sends an invitation.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

FEATURE = "rooms"
KEYS = ("room", "agenda", "calendar_event_ref", "end_at", "cancelled_at")
_ROOM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_EMAIL = re.compile(r"^[^@\s,;:<>]{1,64}@[^@\s,;:<>]{1,253}$")
#: A link the calendar file may carry: http(s), and no whitespace or control character anywhere
#: (a value with a line break would otherwise become a property line of its own).
_URL = re.compile(r"^https?://[^\s\x00-\x1f\x7f]{1,2000}$")
DEFAULT_MINUTES = 30


FEATURES_HEADER = "x-user-features"


def switched_on(env: Optional[Mapping[str, str]] = None, headers: Optional[Mapping[str, str]] = None) -> bool:
    """Is `rooms` on for THIS request? agent-api decides it (its `features` layer, the company's
    `_global/POLICIES.md` included) and says so on the internal tier as ``X-User-Features: rooms`` —
    an `x-user-*` header the identity guard keeps only from the internal tier, never from a person.
    Else `rooms` named in this process's `VEXA_FEATURES` — `all` alone does not switch it on."""
    if headers is not None:
        said = str(headers.get(FEATURES_HEADER) or "")
        if FEATURE in {t.strip().lower() for t in said.split(",")}:
            return True
    raw = str((env if env is not None else os.environ).get("VEXA_FEATURES") or "")
    on = False
    for token in (t.strip().lower() for t in raw.split(",")):
        if token.lstrip("-+") == FEATURE:
            on = not token.startswith("-")
    return on


def refusal() -> dict:
    return {"status": "refused", "reason": "feature_disabled", "feature": FEATURE,
            "instruction": "rooms is switched off on this deployment. Do not retry it another way; say "
                           "it is not available here."}


def parse(payload: dict) -> "tuple[dict, Optional[str]]":
    """``(room_plan, error)`` from a body: only the room keys, validated; ``error`` names the bad one."""
    out: dict = {}
    for key in KEYS:
        if key not in payload:
            continue
        v = payload[key]
        if v is None:
            out[key] = None
            continue
        if not isinstance(v, str) or not v.strip() or len(v) > 512:
            return {}, f"'{key}' must be a non-empty string"
        if key == "room" and not _ROOM.match(v.strip()):
            return {}, "'room' must be a room's session id"
        out[key] = v.strip()
    return out, None


def attendees(payload: dict) -> "tuple[Optional[list], Optional[str]]":
    raw = payload.get("attendees")
    if raw is None:
        return None, None
    if not isinstance(raw, list) or len(raw) > 100 or not all(isinstance(a, str) and _EMAIL.match(a.strip())
                                                              for a in raw):
        return None, "'attendees' must be a list of email addresses"
    return [a.strip().lower() for a in dict.fromkeys(raw)], None


def _dt(value) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _esc(text: str) -> str:
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 §3.1: lines longer than 75 octets continue on the next, indented by one space."""
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75
        while cut > 0 and (b[cut] & 0xC0) == 0x80:      # never split a UTF-8 sequence
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def sync_updates(row: dict, *, event_id: str, status: str, start: Optional[str] = None,
                 end: Optional[str] = None, now: Optional[datetime] = None) -> Optional[dict]:
    """SYNC BACK (PLAN-8H 8.4): the creator's calendar event is the source of truth for a room
    call's time. A change the calendar sensor reports becomes the row's update, or None when this
    row is not that event's room call. ``status`` is ``updated`` (start / end moved) or
    ``cancelled`` (the row is kept, marked ``cancelled_at``)."""
    data = row.get("data") or {}
    if row.get("shared") or not data.get("room") or not event_id or data.get("calendar_event_ref") != event_id:
        return None
    if status == "cancelled":
        if data.get("cancelled_at"):
            return None
        return {"cancelled_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if status != "updated":
        return None
    out: dict = {}
    s, e = _dt(start), _dt(end)
    if s is not None and str(start) != data.get("scheduled_at"):
        out["scheduled_at"] = str(start)
    if e is not None and str(end) != data.get("end_at"):
        out["end_at"] = str(end)
    if data.get("cancelled_at"):
        out["cancelled_at"] = None                        # restored in the calendar
    return out or None


def ics(row: dict, *, host: str = "vexa.ai", now: Optional[datetime] = None) -> Optional[str]:
    """The row as a VCALENDAR with one VEVENT, or None when it has no time."""
    data = row.get("data") or {}
    start = _dt(data.get("scheduled_at"))
    if start is None:
        return None
    end = _dt(data.get("end_at")) or start + timedelta(minutes=DEFAULT_MINUTES)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Vexa//Rooms//EN", "METHOD:REQUEST",
             "BEGIN:VEVENT", f"UID:meeting-{row.get('id')}@{host}", f"DTSTAMP:{stamp}",
             f"DTSTART:{fmt(start)}", f"DTEND:{fmt(end)}",
             f"SUMMARY:{_esc(data.get('title') or 'Call')}"]
    if data.get("agenda"):
        lines.append(f"DESCRIPTION:{_esc('Agenda: ' + str(data['agenda']))}")
    url = str(data.get("constructed_meeting_url") or "")
    if url and _URL.match(url):
        lines.append(f"URL:{url}")
    for a in data.get("attendees") or []:
        addr = str((a.get("email") if isinstance(a, dict) else a) or "")
        # only an address the API itself would accept: one line, no separators
        if addr and _EMAIL.match(addr) and not any(ord(ch) < 32 or ord(ch) == 127 for ch in addr):
            lines.append(f"ATTENDEE;RSVP=TRUE:mailto:{addr}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
