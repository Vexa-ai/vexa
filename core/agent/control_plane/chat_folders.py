"""chat_folders.py — a person's chat folders on their own rail (#1783), kept in the session store.

THE SECTIONS. Every rail is drawn as: **Pinned** (manual order) · **Shared with me** (a chat somebody
else owns and added the person to lands here until they file it elsewhere) · the person's own folders (manual
order; an automatic **Routines** folder is made once and may be renamed or dissolved) · **Ungrouped**
(newest first) · **Hidden** (collapsed by default, at the bottom). Hiding a chat is moving it into
Hidden and un-hiding is moving it out: Hidden is a folder, not a filter.

PER PERSON, NEVER PER CHAT. A row's place is keyed by ``(owner, row)``, where a row is a rail key
(``c:<chat session>`` or ``m:<meeting id>``). On a shared session (ADR-0048) each participant files
it in their own folders; nobody reads, renames or files into another's — a folder id that is not the
caller's is answered exactly like one that does not exist (``no_such_folder``).

ONE PLACE PER ROW. Filing a row anywhere replaces where it was. A row with no filing is Ungrouped,
except a chat somebody else owns, which shows in Shared with me, and a routine's chat, which the
client (and the agent listing) shows in Routines while that folder exists. Moving a row to Ungrouped records it there, so it overrides that default.

DELETING A FOLDER NEVER DELETES A CHAT: its rows are filed in Ungrouped.

THE OLD MARKS. Pin and hide used to be two flags in the session index (``_Sessions.rail_marks``).
The first time a person's folders are read, those marks are filed once (hidden wins over pinned,
pins keep the rail order they had) and never read again. ``marks``/``set_mark`` answer the old
``/api/chat/marks`` shape over the folders, so an older client keeps working.

agent-api is the one writer, through the store's own connection and lock (ADR-0048 decision 2).
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Callable, Iterable, Optional

PINNED, HIDDEN, UNGROUPED, ROUTINES, SHARED = "pinned", "hidden", "ungrouped", "routines", "shared"
BUILTIN = (PINNED, SHARED, UNGROUPED, HIDDEN)
_NAMES = {PINNED: "Pinned", SHARED: "Shared with me", UNGROUPED: "Ungrouped", HIDDEN: "Hidden"}
#: The most folders one person keeps, and the most rows they file. Both bound the carrier.
MAX_FOLDERS = 200
MAX_FILINGS = 5000
NAME_MAX = 60
_FOLDER_ID = re.compile(r"^(?:pinned|shared|hidden|ungrouped|routines|f-[0-9a-f]{12})$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: ``() -> ({pinned: [row], hidden: [row]}, rail_order)`` — the person's old marks, read once.
Legacy = Optional[Callable[[], "tuple[dict, list]"]]


class FolderError(ValueError):
    """A refused folder act. ``reason`` is typed: ``no_such_folder``, ``builtin_folder``,
    ``bad_name``, ``too_many_folders``, ``too_many_rows``."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def _clean_name(name: str) -> str:
    n = " ".join(str(name or "").split())
    if not n or len(n) > NAME_MAX:
        raise FolderError("bad_name", f"a folder name is 1-{NAME_MAX} characters on one line")
    if _CONTROL.search(str(name or "")):
        raise FolderError("bad_name", "a folder name is plain text on one line")
    if n.lower() in _NAMES or n.lower() in {v.lower() for v in _NAMES.values()}:
        raise FolderError("bad_name", f"“{n}” is a built-in section")
    return n


def _ensure(store, owner: str, legacy: Legacy = None) -> None:
    """Make the person's built-in rows and the Routines folder, and file their old marks — once.
    The Ungrouped row is the marker: it is never deleted, so a dissolved Routines stays dissolved.
    Shared with me is built in and came later, so a person made before it gets its row here too."""
    with store._lock:
        if store._x("SELECT 1 FROM agent_session_folder WHERE owner = ? AND id = ?",
                    (owner, UNGROUPED)).fetchone() is not None:
            if store._x("SELECT 1 FROM agent_session_folder WHERE owner = ? AND id = ?",
                        (owner, SHARED)).fetchone() is None:
                store._x("INSERT INTO agent_session_folder (owner, id, name, position, collapsed, created) "
                         "VALUES (?, ?, ?, ?, 0, ?) ON CONFLICT DO NOTHING",
                         (owner, SHARED, _NAMES[SHARED], -1, time.time()))
            return
    old_marks, old_order = ({}, [])
    if legacy is not None:
        try:
            old_marks, old_order = legacy()
        except Exception:  # noqa: BLE001 — an unreadable index leaves the rail unfiled, never broken
            old_marks, old_order = ({}, [])
    now = time.time()
    with store._tx():
        if store._x("SELECT 1 FROM agent_session_folder WHERE owner = ? AND id = ?",
                    (owner, UNGROUPED)).fetchone() is not None:
            return
        for fid, pos, collapsed in ((PINNED, -1, 0), (SHARED, -1, 0), (ROUTINES, 0, 0), (UNGROUPED, 10**6, 0),
                                    (HIDDEN, 10**6 + 1, 1)):
            store._x("INSERT INTO agent_session_folder (owner, id, name, position, collapsed, created) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                     (owner, fid, _NAMES.get(fid, "Routines"), pos, collapsed, now))
        hidden = [str(r) for r in (old_marks or {}).get("hidden") or [] if r]
        rank = {r: i for i, r in enumerate(old_order or [])}
        pinned = sorted((str(r) for r in (old_marks or {}).get("pinned") or [] if r and r not in hidden),
                        key=lambda r: (rank.get(r, len(rank)), r))
        for i, row in enumerate(pinned):
            store._x("INSERT INTO agent_session_filing (owner, row_key, folder, position, filed) "
                     "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", (owner, row, PINNED, float(i), now))
        for row in dict.fromkeys(hidden):
            store._x("INSERT INTO agent_session_filing (owner, row_key, folder, position, filed) "
                     "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", (owner, row, HIDDEN, 0.0, now))


def _folders(store, owner: str) -> list[dict]:
    with store._lock:
        rows = store._x("SELECT id, name, position, collapsed, created FROM agent_session_folder "
                        "WHERE owner = ?", (owner,)).fetchall()
    out = [{"id": r[0], "name": r[1], "position": int(r[2]), "collapsed": bool(r[3]),
            "builtin": r[0] in BUILTIN, "created": float(r[4])} for r in rows]

    def key(f):
        if f["id"] == PINNED:
            return (0, 0, 0.0)
        if f["id"] == SHARED:
            return (0, 1, 0.0)
        if f["id"] == UNGROUPED:
            return (2, 0, 0.0)
        if f["id"] == HIDDEN:
            return (3, 0, 0.0)
        return (1, f["position"], f["created"])
    out.sort(key=key)
    return out


def _owned(store, owner: str, folder: str) -> dict:
    if not _FOLDER_ID.fullmatch(str(folder or "")):
        raise FolderError("no_such_folder", "no such folder")
    f = next((x for x in _folders(store, owner) if x["id"] == folder), None)
    if f is None:
        raise FolderError("no_such_folder", "no such folder")
    return f


def listing(store, owner: str, legacy: Legacy = None) -> dict:
    """``{folders: [{id, name, builtin, collapsed, position}], filings: {row: folder},
    order: {folder: [row]}}`` — every section in drawing order, where each filed row is, and the
    manual order of each folder's rows (the client uses Pinned's; the rest it draws newest first)."""
    _ensure(store, owner, legacy)
    with store._lock:
        rows = store._x("SELECT row_key, folder, position, filed FROM agent_session_filing WHERE owner = ?",
                        (owner,)).fetchall()
    rows = sorted(rows, key=lambda r: (float(r[2]), float(r[3]), r[0]))
    order: dict[str, list[str]] = {}
    for r in rows:
        order.setdefault(r[1], []).append(r[0])
    return {"folders": [{k: f[k] for k in ("id", "name", "builtin", "collapsed", "position")}
                        for f in _folders(store, owner)],
            "filings": {r[0]: r[1] for r in rows}, "order": order}


def create(store, owner: str, name: str, legacy: Legacy = None) -> dict:
    """A new named folder at the end of the person's folders — or the one they already have under
    that name (case-insensitive), so asking twice makes one. Returns ``{id, name, ..., created}``
    where ``created`` says whether this call made it."""
    clean = _clean_name(name)
    _ensure(store, owner, legacy)
    mine = _folders(store, owner)
    same = next((f for f in mine if not f["builtin"] and f["name"].lower() == clean.lower()), None)
    if same is not None:
        return {**{k: same[k] for k in ("id", "name", "builtin", "collapsed", "position")}, "created": False}
    if sum(1 for f in mine if not f["builtin"]) >= MAX_FOLDERS:
        raise FolderError("too_many_folders", f"at most {MAX_FOLDERS} folders")
    fid = f"f-{uuid.uuid4().hex[:12]}"
    pos = max([f["position"] for f in mine if not f["builtin"]] + [0]) + 1
    with store._tx():
        store._x("INSERT INTO agent_session_folder (owner, id, name, position, collapsed, created) "
                 "VALUES (?, ?, ?, ?, 0, ?)", (owner, fid, clean, pos, time.time()))
    return {"id": fid, "name": clean, "builtin": False, "collapsed": False, "position": pos, "created": True}


def update(store, owner: str, folder: str, *, name: Optional[str] = None,
           collapsed: Optional[bool] = None, position: Optional[int] = None) -> dict:
    """Rename, collapse/expand, or move a folder to ``position`` (0 = first of the person's own
    folders). A built-in section only collapses."""
    _ensure(store, owner)
    f = _owned(store, owner, folder)
    if f["builtin"] and (name is not None or position is not None):
        raise FolderError("builtin_folder", f"{f['name']} is a built-in section: it only collapses")
    clean = _clean_name(name) if name is not None else None
    with store._tx():
        if clean is not None:
            store._x("UPDATE agent_session_folder SET name = ? WHERE owner = ? AND id = ?", (clean, owner, folder))
        if collapsed is not None:
            store._x("UPDATE agent_session_folder SET collapsed = ? WHERE owner = ? AND id = ?",
                     (1 if collapsed else 0, owner, folder))
    if position is not None:
        own = [x["id"] for x in _folders(store, owner) if not x["builtin"] and x["id"] != folder]
        own.insert(max(0, min(int(position), len(own))), folder)
        with store._tx():
            for i, fid in enumerate(own):
                store._x("UPDATE agent_session_folder SET position = ? WHERE owner = ? AND id = ?", (i + 1, owner, fid))
    return next(x for x in listing(store, owner)["folders"] if x["id"] == folder)


def delete(store, owner: str, folder: str) -> list[str]:
    """Dissolve one of the person's folders. Its rows are filed in Ungrouped — a chat is never
    deleted with its folder. Returns the rows that moved."""
    _ensure(store, owner)
    f = _owned(store, owner, folder)
    if f["builtin"]:
        raise FolderError("builtin_folder", f"{f['name']} is a built-in section")
    with store._tx():
        rows = [r[0] for r in store._x("SELECT row_key FROM agent_session_filing WHERE owner = ? AND folder = ?",
                                       (owner, folder)).fetchall()]
        store._x("UPDATE agent_session_filing SET folder = ?, filed = ? WHERE owner = ? AND folder = ?",
                 (UNGROUPED, time.time(), owner, folder))
        store._x("DELETE FROM agent_session_folder WHERE owner = ? AND id = ?", (owner, folder))
    return rows


def file_rows(store, owner: str, rows: Iterable[str], to: str, *, before: Optional[str] = None,
         legacy: Legacy = None) -> dict:
    """File ``rows`` in folder ``to`` (a folder id, or ``pinned`` / ``ungrouped`` / ``hidden``),
    each leaving wherever it was. ``before`` places them ahead of that row in the folder's manual
    order; otherwise they go at its end. Returns the new ``listing``."""
    rows = [str(r) for r in dict.fromkeys(rows) if r]
    _ensure(store, owner, legacy)
    _owned(store, owner, to)
    now = time.time()
    with store._tx():
        have = {r[0]: (r[1], float(r[2])) for r in store._x(
            "SELECT row_key, folder, position FROM agent_session_filing WHERE owner = ?", (owner,)).fetchall()}
        if len(set(have) | set(rows)) > MAX_FILINGS:
            raise FolderError("too_many_rows", f"at most {MAX_FILINGS} filed rows")
        current = sorted((r for r, (f, _) in have.items() if f == to and r not in rows),
                         key=lambda r: have[r][1])
        at = current.index(before) if before in current else len(current)
        ordered = current[:at] + rows + current[at:]
        for i, row in enumerate(ordered):
            if row in rows:
                if row in have:
                    store._x("UPDATE agent_session_filing SET folder = ?, position = ?, filed = ? "
                             "WHERE owner = ? AND row_key = ?", (to, float(i), now, owner, row))
                else:
                    store._x("INSERT INTO agent_session_filing (owner, row_key, folder, position, filed) "
                             "VALUES (?, ?, ?, ?, ?)", (owner, row, to, float(i), now))
            elif have[row][1] != float(i):
                store._x("UPDATE agent_session_filing SET position = ? WHERE owner = ? AND row_key = ?",
                         (float(i), owner, row))
    return listing(store, owner)


def forget(store, owner: str, row: str) -> None:
    """Drop a row's filing (its chat was deleted)."""
    with store._tx():
        store._x("DELETE FROM agent_session_filing WHERE owner = ? AND row_key = ?", (owner, row))


def marks(store, owner: str, legacy: Legacy = None) -> dict:
    """The old ``{pinned: [row], hidden: [row]}``, read off the folders (pinned in its order)."""
    got = listing(store, owner, legacy)
    return {"pinned": list(got["order"].get(PINNED, [])), "hidden": sorted(got["order"].get(HIDDEN, []))}


def set_mark(store, owner: str, row: str, *, pinned: Optional[bool] = None,
             hidden: Optional[bool] = None, legacy: Legacy = None) -> dict:
    """The old pin/hide verb over folders: ``True`` files the row there; ``False`` takes it out of
    there (back to Ungrouped) only if that is where it is. Hidden wins when both are set."""
    where = listing(store, owner, legacy)["filings"].get(row)
    if hidden:
        file_rows(store, owner, [row], HIDDEN)
    elif pinned:
        file_rows(store, owner, [row], PINNED)
    for flag, folder in ((pinned, PINNED), (hidden, HIDDEN)):
        if flag is False and where == folder:
            forget(store, owner, row)
    return marks(store, owner)
