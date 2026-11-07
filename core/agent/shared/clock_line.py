"""ONE spelling of "now" for every place it enters a turn (Vexa-ai/vexa#1783).

`now` reaches an agent's turn through the timeline block (flows renders it; the worker refreshes it
per turn), the `<schedule>` block agent-api folds into a chat prompt, and the `current_time` tool.
All three spell it with `now_label`, so the turn never holds two clocks that disagree.

The spelling names the person's zone, its abbreviation, its UTC offset AND the UTC time:

    Tuesday 13 October 2026, 19:06 Europe/Lisbon (WEST, UTC+01:00); 18:06 UTC

Friction 2026-10-13: the turn said "19:05 WEST" — correct — while a tool's timestamps were UTC
(`…T18:03Z`). With no offset beside the local clock the agent read 18:03 as local and concluded an
hour of transcript was missing. Every tool timestamp ending in `Z` or `+00:00` is UTC; the UTC half
of this line is what it is compared against.

The offset is the zone's on THAT instant (zoneinfo), never a remembered one, so a turn either side
of a daylight-saving change is right. An unknown or unset zone is UTC, said plainly. Mirrored, by
format and by test, in `core/flows/src/flows_timeline/render.py::now_label` — flows ships in its
own image and cannot import this package.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

UTC = timezone.utc


def _zone(tz: str):
    if not tz or tz.upper() in ("UTC", "ETC/UTC", "Z"):
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz)
    except Exception:  # noqa: BLE001 — a bad zone is a setting, never a crash
        return None


def _aware(now: Optional[datetime]) -> datetime:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware; a naive time is how an offset gets applied twice")
    return now


def utc_offset(tz: str, now: Optional[datetime] = None) -> str:
    """`UTC+01:00` — the zone's offset at `now` (UTC+00:00 for no zone)."""
    now = _aware(now)
    z = _zone(tz)
    off = now.astimezone(z).utcoffset() if z else None
    secs = int(off.total_seconds()) if off else 0
    sign = "+" if secs >= 0 else "-"
    secs = abs(secs)
    return f"UTC{sign}{secs // 3600:02d}:{secs % 3600 // 60:02d}"


def now_label(tz: str, now: Optional[datetime] = None) -> str:
    """The one spelling of now — see the module docstring."""
    now = _aware(now)
    u = now.astimezone(UTC)
    z = _zone(tz)
    if z is None:
        tail = "" if (tz or "").upper() in ("UTC", "ETC/UTC", "Z") else " (no timezone set)"
        return f"{u.strftime('%A %d %B %Y')}, {u.strftime('%H:%M')} UTC{tail}"
    local = now.astimezone(z)
    return (f"{local.strftime('%A %d %B %Y')}, {local.strftime('%H:%M')} {tz} "
            f"({local.tzname() or tz}, {utc_offset(tz, now)}); {u.strftime('%H:%M')} UTC")
