"""rooms_overview.py — a workspace's rooms as its reader is shown them (held-label-rooms-1018).

The agent was asked to put "the rooms and agents that exist here" on a workspace's page and wrote
*"I couldn't list rooms directly"*: `GET /api/rooms` existed for the app, but no tool reached it, and
it named neither the people in a room nor whether a call was running. This module answers, for one
workspace and one reader, every room that reader may see with what a page about the workspace needs:

* the room's name, its visibility, and the reference that opens it (``[[room:<id>]]``);
* its people (humans, by name — never an address), from the one authority on room membership
  (``room_membership.members``);
* whether a call is live in it right now;
* the recurring meetings bound to it (or to the workspace), with their joining links.

WHO SEES WHAT — the existing rule, in one place (``visible_rooms``; ``GET /api/rooms`` uses it too):
a member of the workspace sees its OPEN rooms; anyone sees the rooms they are in; a closed room they
are not in is not listed, exactly as if it did not exist. Each row is checked once more through
``rooms.visible_to`` before it is described.

A TURN OTHER PEOPLE READ (a room's agent, a shared chat) sees only the OPEN rooms of the workspace it
was dispatched into — never a closed room its owner happens to be in, and never the meetings, which
are a person-wide read (``shared_view``). The route confines the workspace (`routers/rooms.py`).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from control_plane import rooms as rooms_mod

log = logging.getLogger("agent_api.rooms_overview")

#: How many people a room row names; the rest are counted (``more``). A page lists a handful.
MAX_NAMED = 25


def visible_rooms(store, workspace: str, subject: str, *, is_member: Callable[[str, str], bool]) -> list[dict]:
    """The rooms of ``workspace`` that ``subject`` can see: the open ones (for its members) and the
    ones they are in. ``{id, title, owner, last_active, visibility, member}``, newest first."""
    mine = {r["id"]: r for r in store.list_for(subject)}
    out: list[dict] = []
    try:
        member = bool(is_member(workspace, subject))
    except Exception:  # noqa: BLE001 — an unreadable member list is not a membership
        member = False
    if member:
        for r in store.open_rooms(workspace):
            out.append({**r, "visibility": "open", "member": r["id"] in mine})
    seen = {r["id"] for r in out}
    for sid, r in mine.items():
        room = store.room(sid)
        if sid in seen or not room or room.get("workspace") != workspace:
            continue
        out.append({"id": sid, "title": r.get("title") or "", "owner": r.get("owner"),
                    "last_active": r.get("last_active"), "visibility": room["visibility"],
                    "member": True})
    return out


def shared_view(store, workspace: str) -> list[dict]:
    """What a turn other people read may list: the workspace's OPEN rooms, nothing else."""
    return [{**r, "visibility": "open", "member": False} for r in store.open_rooms(workspace)]


# ── recurring meetings ────────────────────────────────────────────────────────────────────────────

def _data(row) -> dict:
    d = row.get("data") if isinstance(row, dict) else None
    return d if isinstance(d, dict) else {}


def is_recurring(row: dict) -> bool:
    """Is this meeting row one occurrence of a recurring calendar event? The calendar feed stamps a
    series on it (``series_master``, a recurrence rule or id, or a workspace inherited from the
    series)."""
    d = _data(row)
    if d.get("workspace_source") == "series":
        return True
    for src in d.get("calendar_sources") or []:
        ev = (src or {}).get("event") if isinstance(src, dict) else None
        if not isinstance(ev, dict):
            continue
        if ev.get("series_master"):
            return True
        props = ((ev.get("component") or {}).get("properties") or {}) if isinstance(ev.get("component"), dict) else {}
        if isinstance(props, dict) and ({"RRULE", "RECURRENCE-ID", "RDATE"} & set(props)):
            return True
    return False


def _link(row: dict) -> str:
    d = _data(row)
    return str(d.get("meeting_url") or row.get("meeting_url") or row.get("constructed_meeting_url") or "").strip()


def recurring_meetings(rows: Iterable[dict], workspace: str, room_ids: Iterable[str]) -> dict:
    """The recurring meetings bound to ``workspace`` or to one of ``room_ids``, one entry per series
    (its calendar event), soonest next occurrence first: ``{"": [...workspace-level], <room>: [...]}``.
    Each entry: ``{title, link, platform, next}`` — ``next`` the soonest occurrence still ahead, or
    the latest one when none is."""
    rooms = set(room_ids)
    now = time.time()
    series: dict = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        d = _data(row)
        room = str(d.get("room") or "")
        home = room if room in rooms else ("" if str(d.get("workspace_id") or "") == workspace else None)
        if home is None or not is_recurring(row):
            continue
        link = _link(row)
        key = (home, str(d.get("calendar_uid") or "") or link or str(row.get("id")))
        when = str(d.get("scheduled_at") or row.get("start_time") or "")
        entry = series.setdefault(key, {"title": str(d.get("title") or row.get("title") or "").strip(),
                                        "link": link, "platform": str(row.get("platform") or ""),
                                        "next": "", "_t": None})
        if link and not entry["link"]:
            entry["link"] = link
        # the soonest occurrence still ahead wins; with none ahead, the latest
        rank = _rank(_epoch(when), now)
        if entry["_t"] is None or rank < entry["_t"]:
            entry["_t"], entry["next"] = rank, when
    out: dict = {}
    for (home, _k), e in series.items():
        out.setdefault(home, []).append(e)
    for home in out:
        out[home].sort(key=lambda e: e["_t"] or _rank(None, now))
        out[home] = [{k: v for k, v in e.items() if k != "_t"} for e in out[home]]
    return out


def _rank(t: Optional[float], now: float) -> tuple:
    """Order of occurrences: ahead (soonest first), then past (latest first), then undated."""
    if t is None:
        return (2, 0.0)
    return (0, t) if t >= now else (1, -t)


def _epoch(iso: str) -> Optional[float]:
    if not iso:
        return None
    from datetime import datetime  # noqa: PLC0415
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ── one room, described ───────────────────────────────────────────────────────────────────────────

def roster_names(root: Path, workspace: str) -> dict:
    """``{subject: name}`` from the workspace's own roster — its entry's name, else the address's
    local part. One read for every room of the workspace."""
    from control_plane import workspace_membership as m  # noqa: PLC0415
    try:
        rows = m.read_members(Path(root), workspace)
    except Exception:  # noqa: BLE001 — names are a courtesy; the authority decided who is listed
        return {}
    out = {}
    for r in rows or []:
        who = str((r or {}).get("subject") or "")
        if who:
            name = str(r.get("name") or "").strip() or str(r.get("email") or "").split("@", 1)[0]
            out[who] = name
    return out


def describe(store, root: Path, row: dict, *, names: dict, name_of: Callable[[str], str],
             meetings: Optional[list]) -> dict:
    """A room row as the reader is shown it. ``meetings`` None = not read for this reader."""
    from control_plane import room_membership  # noqa: PLC0415
    sid = row["id"]
    people: list[str] = []
    agents = 0
    unreadable = False
    try:
        for x in room_membership.members(store, root, sid):
            if x.get("kind") == "human":
                who = str(x["principal"])
                people.append(names.get(who) or name_of(who) or who)
            else:
                agents += 1
    except Exception:  # noqa: BLE001 — an unreadable roster names nobody (FD3)
        unreadable = True
    try:
        call = store.active_call(sid)
    except Exception:  # noqa: BLE001
        call = None
    out = {"id": sid, "ref": f"[[room:{sid}]]", "name": row.get("title") or "",
           "visibility": row.get("visibility") or "", "member": bool(row.get("member")),
           "people": people[:MAX_NAMED], "people_count": len(people),
           "call_live": bool(call), "last_active": row.get("last_active")}
    if len(people) > MAX_NAMED:
        out["more"] = len(people) - MAX_NAMED
    if agents:
        out["agents"] = agents
    if call:
        out["call_started"] = call.get("started")
    if unreadable:
        out["people_unreadable"] = True
    if meetings is not None:
        out["recurring_meetings"] = meetings
    return out


def overview(store, root: Path, workspace: str, subject: str, *, shared: bool,
             is_member: Callable[[str, str], bool], name_of: Callable[[str], str],
             meeting_rows: Optional[Callable[[], list]] = None) -> dict:
    """Every room of ``workspace`` this reader may see, described. ``shared``: a turn other people
    read (open rooms only, no meetings). ``meeting_rows``: the reader's own meetings, read once."""
    rows = shared_view(store, workspace) if shared else visible_rooms(store, workspace, subject,
                                                                      is_member=is_member)
    if not shared:
        # the belt: each row is one this reader may see, by the room rule itself
        rows = [r for r in rows if rooms_mod.visible_to(store, r["id"], subject, is_member=is_member)]
    by_home: Optional[dict] = None
    if not shared and meeting_rows is not None:
        try:
            by_home = recurring_meetings(meeting_rows() or [], workspace, [r["id"] for r in rows])
        except Exception as exc:  # noqa: BLE001 — meetings are an enrichment, never a gate
            log.warning("rooms overview: meetings not read (%s)", type(exc).__name__)
            by_home = None
    names = roster_names(root, workspace)
    described = [describe(store, root, r, names=names, name_of=name_of,
                          meetings=(by_home.get(r["id"], []) if by_home is not None else None))
                 for r in rows]
    out = {"workspace": workspace, "rooms": described}
    if by_home is not None:
        out["recurring_meetings"] = by_home.get("", [])
    elif shared:
        out["note"] = ("Only this workspace's open rooms are listed here, and no meetings: other "
                       "people read this conversation.")
    return out
