"""THE DESK README'S `Now` SECTION, built from the same dated facts the timeline reads.

PRD decision 26.4 (founder, 2026-09-02 14:0xZ): the desk README *is* the desk, *"mostly links to
the other cards in different workspaces"*, and one of its sections is `Now` — next meetings, open
commitments. Decision 31 §3: *"the write-back phase files dated facts so the desk README's `Now`
and the timeline agree"*.

AGREE is the requirement, and it is why this module exists instead of a second query. The timeline
reads the flows engine; the desk README reads the workspace. If the README derived its `Now` from
its own prose — "the meeting is Thursday", written by a model that read a note — the two would
drift silently the first time a meeting moved, and the person would be told two different things by
one product on one screen. So the meeting PAGE carries the dates, `entity_upsert` is the only thing
that writes them (`shared/entities.py`, the `dates` argument), and both readers read facts rather
than sentences.

Three frontmatter keys, all ISO-8601 UTC, all optional:

    scheduled_at:        when it is meant to happen        → a next meeting
    held_at:             when it actually ran              → it happened
    report_delivered_at: when the write-up reached them    → the commitment is closed

An OPEN COMMITMENT is exactly `held_at` set and `report_delivered_at` absent: the meeting happened
and nothing has been delivered about it. That is a fact with a shape, not a judgement, which is the
only kind of thing a section like this can be built out of.

This module does NOT write the README. Whoever owns that file calls `render_now(root)` and drops
the result between its markers — one loop, one write surface.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Optional

from shared.entities import split_frontmatter

MEETING_KIND = "meeting"
DATE_FIELDS = ("scheduled_at", "held_at", "report_delivered_at")

UTC = datetime.timezone.utc
_FM_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*)\s*:\s*(.*)$")

# How far back a meeting whose write-up never arrived keeps asking. Beyond this it is history, not
# a commitment — a `Now` section that still lists last month is a section people stop reading.
OPEN_WINDOW_DAYS = 14


def _epoch(value: str) -> Optional[float]:
    text = str(value or "").strip().strip("'\"")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def read_page(path: Path) -> dict:
    """`{title, path, scheduled_at, held_at, report_delivered_at}` — epochs, absent keys omitted."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    fm, _body = split_frontmatter(raw)
    out: dict = {"path": str(path), "title": path.stem}
    for line in fm:
        m = _FM_LINE.match(line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "title" and value:
            out["title"] = value.strip("'\"")
        elif key == "template" and value.lower() in ("true", "yes"):
            return {}                  # a shape is not a meeting (the entity-index rule, same list)
        elif key in DATE_FIELDS:
            at = _epoch(value)
            if at is not None:
                out[key] = at
    return out


def meetings(root) -> list[dict]:
    """Every meeting page on this desk that carries at least one date. Order is not decided here."""
    folder = Path(root) / "kg" / "entities" / MEETING_KIND
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob("*.md")):
        page = read_page(f)
        if page and any(k in page for k in DATE_FIELDS):
            out.append(page)
    return out


def now_rows(root, *, now: Optional[float] = None, ahead: int = 5,
             open_window_days: int = OPEN_WINDOW_DAYS) -> dict:
    """`{"next": [...], "open": [...]}` — the two lists decision 26.4 names, from the pages.

    `next`  — `scheduled_at` still ahead, soonest first, and never one that already has a
              `held_at`: a meeting that ran is not still coming, whatever its calendar row says.
    `open`  — `held_at` inside the window with no `report_delivered_at`. Most recent first, because
              the write-up nobody has seen yet is the one most likely to still be wanted.
    """
    now = datetime.datetime.now(UTC).timestamp() if now is None else float(now)
    floor = now - open_window_days * 86400
    pages = meetings(root)
    nxt = sorted((p for p in pages
                  if p.get("scheduled_at", 0) > now and "held_at" not in p),
                 key=lambda p: p["scheduled_at"])[:ahead]
    opn = sorted((p for p in pages
                  if floor <= p.get("held_at", 0) <= now and "report_delivered_at" not in p),
                 key=lambda p: p["held_at"], reverse=True)
    return {"next": nxt, "open": opn}


def _stamp(epoch: float, tz: str = "") -> str:
    z = UTC
    if tz:
        try:
            import zoneinfo
            z = zoneinfo.ZoneInfo(tz)
        except Exception:  # noqa: BLE001
            z = UTC
    t = datetime.datetime.fromtimestamp(float(epoch), z)
    return t.strftime("%a %d %b %H:%M ") + (t.tzname() or "UTC")


def render_now(root, *, now: Optional[float] = None, tz: str = "", ahead: int = 5) -> str:
    """The `Now` section body — LINKS, per decision 26.4, never prose about the links.

    Returned without its heading and without the markers: the README's owner puts it where it goes.
    Empty lists are stated, not hidden. "Nothing scheduled" is information; a section that silently
    disappears when it has nothing to say reads as a bug the first time someone looks for it.
    """
    rows = now_rows(root, now=now, ahead=ahead)
    out: list[str] = []
    if rows["next"]:
        for p in rows["next"]:
            out.append(f"- {_stamp(p['scheduled_at'], tz)} — [[{p['title']}]]")
    else:
        out.append("- Nothing scheduled.")
    if rows["open"]:
        out.append("")
        for p in rows["open"]:
            out.append(f"- [[{p['title']}]] — held {_stamp(p['held_at'], tz)}, no write-up yet")
    return "\n".join(out) + "\n"
