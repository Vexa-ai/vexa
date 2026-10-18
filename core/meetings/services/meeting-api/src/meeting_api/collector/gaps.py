"""Transcript gaps on the reader's side (prod-cutover R4).

The lifecycle receiver persists the bot's report of captured audio it never transcribed at
``meeting.data.transcript_gaps`` (epoch-second ranges + reason). No 0.12 response carries it
(``projection.OPT_IN_KEYS``); a reader that asks — ``GET /transcripts/…?include_gaps=true`` — gets
this rendering as a top-level ``gaps`` list: the stored range, placed on the meeting's own clock
(``start_offset_s``/``end_offset_s`` from the meeting's ``start_time``), with the quiet marker a
transcript view shows in place of the words that are missing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _epoch(iso: Any) -> Optional[float]:
    if isinstance(iso, datetime):
        dt = iso if iso.tzinfo else iso.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if not isinstance(iso, str) or not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def clock(seconds: float) -> str:
    """``mm:ss`` (``h:mm:ss`` past an hour) for a non-negative offset."""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_gaps(raw: Any, meeting_start: Any) -> List[Dict[str, Any]]:
    """The stored gaps as a reader receives them, ordered by start. Entries that are not well-formed
    ranges are skipped (the receiver already refuses them; this is the read edge's own guard)."""
    if not isinstance(raw, list):
        return []
    origin = _epoch(meeting_start)
    out: List[Dict[str, Any]] = []
    for g in raw:
        if not isinstance(g, dict):
            continue
        start, end = g.get("start"), g.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or not end > start:
            continue
        item: Dict[str, Any] = {
            "start": start,
            "end": end,
            "absolute_start_time": g.get("absolute_start_time"),
            "absolute_end_time": g.get("absolute_end_time"),
            "reason": g.get("reason") or "unspecified",
        }
        if g.get("speaker"):
            item["speaker"] = g["speaker"]
        # Epoch-second ranges sit on the meeting's clock; a range already relative (a producer that
        # timed from zero) is used as is.
        if start >= 1e9 and origin is not None:
            a, b = start - origin, end - origin
        elif start < 1e9:
            a, b = float(start), float(end)
        else:
            a = b = None
        if a is not None and b is not None:
            item["start_offset_s"] = round(max(0.0, a), 3)
            item["end_offset_s"] = round(max(0.0, b), 3)
            item["label"] = f"gap {clock(a)}–{clock(b)} (not transcribed)"
        else:
            item["label"] = "gap (not transcribed)"
        out.append(item)
    out.sort(key=lambda x: x["start"])
    return out
