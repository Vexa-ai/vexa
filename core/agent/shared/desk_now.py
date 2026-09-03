"""THE DESK README'S `Now` SECTION, built from dated FACTS — the one implementation, the one renderer.

PRD decision 26.4 (founder, 2026-09-02 14:0xZ): the desk README *is* the desk, *"mostly links to
the other cards in different workspaces"*, and one of its sections is `Now` — next meetings, open
commitments. Decision 31 §3: *"the write-back phase files dated facts so the desk README's `Now`
and the timeline agree"*.

AGREE is the requirement, and it is why this module exists instead of a second query. The timeline
reads the flows engine; the desk README reads the workspace. If the README derived its `Now` from
its own prose — "the meeting is Thursday", written by a model that read a note — the two would
drift silently the first time a meeting moved, and the person would be told two different things by
one product on one screen. So the PAGE carries the dates, `entity_upsert` is the only thing that
writes them (`shared/entities.py`, the `dates` argument), and both readers read facts rather than
sentences.

⚠ THERE WERE TWO OF THESE, AND THE OTHER ONE SCRAPED PROSE. `desk_readme._now_rows` shipped first
and found a meeting's date by looking for an ISO string in its title or body, and a commitment by
matching `## Committed`-shaped headings and pulling dates out of their bullets. It worked on the
notes the fixtures happen to contain and it is the exact drift this docstring was written to warn
about: a date is whatever a model last typed, a commitment is whatever a heading was called, and
nothing that moves a meeting can move the README. It is gone. This module is now the only answer to
"what is Now", and `desk_readme` is the only thing that writes the file it lands in — one writer,
one renderer (coordinator ruling, 2026-09-02).

Four frontmatter keys, all ISO-8601 UTC, all optional, all written ONLY by `entity_upsert(dates=)`:

    scheduled_at:        when it is meant to happen        → a next meeting
    held_at:             when it actually ran              → it happened
    report_delivered_at: when the write-up reached them    → the commitment is closed
    due_at:              when something is owed BY         → a dated commitment

An OPEN COMMITMENT is exactly `held_at` set and `report_delivered_at` absent: the meeting happened
and nothing has been delivered about it. That is a fact with a shape, not a judgement, which is the
only kind of thing a section like this can be built out of. A DATED COMMITMENT is `due_at` still
ahead — and it is a field precisely so that "circulate the charter by the 20th" reaches `Now`
because the write-back phase FILED it with a source, never because a regex found a date in a
sentence somebody wrote.

Nothing here writes the README. Whoever owns that file calls `render_now(...)` and drops the result
between its markers.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Iterable, Optional

# ONE closed set, defined where it is written and imported where it is read. Two copies of a
# frontmatter contract is the same drift in miniature: the writer gains a key, the reader never
# hears about it, and the section quietly stops showing a whole class of fact.
from workspaces.shared.entities import DATE_FIELDS, ENTITIES_DIR, KINDS, split_frontmatter
from workspaces.shared.links import format_ref

MEETING_KIND = "meeting"

UTC = datetime.timezone.utc
_FM_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*)\s*:\s*(.*)$")

# How far back a meeting whose write-up never arrived keeps asking. Beyond this it is history, not
# a commitment — a `Now` section that still lists last month is a section people stop reading.
OPEN_WINDOW_DAYS = 14

# The caps. `Now` is the top of the page a person opens without asking for it; past about a dozen
# rows it stops being "what is now" and becomes a list they scroll past.
AHEAD_MAX = 5        # next meetings
OPEN_MAX = 5         # meetings held with no write-up delivered
DUE_MAX = 10         # things owed by a date


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


def read_page(path: Path, *, workspace: str = "", home: bool = True) -> dict:
    """`{title, slug, path, workspace, home, <date fields>}` — epochs, absent keys omitted.

    `workspace`/`home` ride along so the renderer can form the right link without a second read:
    a card on this desk is `[[Title]]`, a card in another mounted workspace is
    `[[ws:<workspace-id>/<entity-id>]]` (PRD decision 26.2)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    fm, _body = split_frontmatter(raw)
    out: dict = {"path": str(path), "title": path.stem, "slug": path.stem,
                 "workspace": workspace, "home": home}
    for line in fm:
        m = _FM_LINE.match(line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "title" and value:
            out["title"] = value.strip("'\"")
        elif key == "id" and value:
            out["slug"] = value.strip("'\"")
        elif key == "template" and value.lower() in ("true", "yes"):
            return {}                  # a shape is not a meeting (the entity-index rule, same list)
        elif key in DATE_FIELDS:
            at = _epoch(value)
            if at is not None:
                out[key] = at
    return out


def _roots(where) -> list[dict]:
    """Accept a single workspace path OR the mount set. One function, two call shapes.

    A bare path is the single-desk case (and the shape every caller had before `Now` learned about
    other workspaces); a list of `{path, id}` is the mount set, where exactly the entry whose id
    matches `home_id` is rendered with the in-workspace link form."""
    if isinstance(where, (str, Path)):
        return [{"path": str(where), "id": "", "home": True}]
    return [{"path": str(m.get("path") or ""), "id": str(m.get("id") or ""),
             "home": bool(m.get("home"))}
            for m in (where or []) if isinstance(m, dict) and m.get("path")]


def dated_pages(where, *, kinds: Iterable[str] = KINDS, home_id: str = "") -> list[dict]:
    """Every entity page carrying at least one date field, across every workspace given.

    All kinds, not only `meeting`: a `due_at` belongs wherever the thing that is owed lives, and
    restricting the scan to meetings would silently drop every commitment filed on a project or a
    decision page. Order is not decided here."""
    out: list[dict] = []
    for mount in _roots(where):
        base = Path(mount["path"]) / ENTITIES_DIR
        if not base.is_dir():
            continue
        home = mount["home"] or (bool(home_id) and mount["id"] == home_id) or not mount["id"]
        for kind in kinds:
            folder = base / kind
            if not folder.is_dir():
                continue
            for f in sorted(folder.glob("*.md")):
                if f.name == "index.md":
                    continue
                page = read_page(f, workspace=mount["id"], home=home)
                if page and any(k in page for k in DATE_FIELDS):
                    page["kind"] = kind
                    out.append(page)
    return out


def meetings(where, *, home_id: str = "") -> list[dict]:
    """Every MEETING page carrying at least one date. Kept as its own name because that is what the
    timeline and the tests ask for; `dated_pages` is the general case."""
    return dated_pages(where, kinds=(MEETING_KIND,), home_id=home_id)


def now_rows(where, *, now: Optional[float] = None, ahead: int = AHEAD_MAX,
             open_window_days: int = OPEN_WINDOW_DAYS, home_id: str = "") -> dict:
    """`{"next": [...], "open": [...], "due": [...]}` — decision 26.4's section, from the pages.

    `next`  — a MEETING whose `scheduled_at` is still ahead, soonest first, and never one that
              already has a `held_at`: a meeting that ran is not still coming, whatever its calendar
              row says.
    `open`  — a MEETING whose `held_at` is inside the window with no `report_delivered_at`. Most
              recent first, because the write-up nobody has seen yet is the one most likely to still
              be wanted.
    `due`   — ANY page whose `due_at` is still ahead, soonest first. A field, never a sentence: the
              write-back phase files it with a source, and nothing else can put a commitment here.
    """
    now = datetime.datetime.now(UTC).timestamp() if now is None else float(now)
    floor = now - open_window_days * 86400
    pages = dated_pages(where, home_id=home_id)
    mtgs = [p for p in pages if p.get("kind") == MEETING_KIND]
    return {
        "next": sorted((p for p in mtgs
                        if p.get("scheduled_at", 0) > now and "held_at" not in p),
                       key=lambda p: p["scheduled_at"])[:ahead],
        "open": sorted((p for p in mtgs
                        if floor <= p.get("held_at", 0) <= now and "report_delivered_at" not in p),
                       key=lambda p: p["held_at"], reverse=True)[:OPEN_MAX],
        "due": sorted((p for p in pages if p.get("due_at", 0) > now),
                      key=lambda p: p["due_at"])[:DUE_MAX],
    }


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


def link_to(page: dict) -> str:
    """`[[Title]]` on this desk; `[[ws:<workspace-id>/<entity-id>]]` anywhere else (decision 26.2)."""
    return (f"[[{page.get('title')}]]" if page.get("home") or not page.get("workspace")
            else format_ref(str(page["workspace"]), str(page.get("slug") or "")))


def render_now(where, *, now: Optional[float] = None, tz: str = "", ahead: int = AHEAD_MAX,
               home_id: str = "") -> str:
    """The `Now` section body — LINKS, per decision 26.4, never prose about the links.

    Returned without its heading and without the markers: the README's owner puts it where it goes.
    Empty lists are stated, not hidden. "Nothing scheduled" is information; a section that silently
    disappears when it has nothing to say reads as a bug the first time someone looks for it.
    """
    rows = now_rows(where, now=now, ahead=ahead, home_id=home_id)
    out: list[str] = []
    if rows["next"]:
        for p in rows["next"]:
            out.append(f"- {_stamp(p['scheduled_at'], tz)} — {link_to(p)}")
    else:
        out.append("- Nothing scheduled.")
    if rows["due"]:
        out.append("")
        for p in rows["due"]:
            out.append(f"- due {_stamp(p['due_at'], tz)} — {link_to(p)}")
    if rows["open"]:
        out.append("")
        for p in rows["open"]:
            out.append(f"- {link_to(p)} — held {_stamp(p['held_at'], tz)}, no write-up yet")
    return "\n".join(out) + "\n"
