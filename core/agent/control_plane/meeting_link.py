"""meeting_link.py — a link to a meeting's notes page, answered as the MEETING, per reader.

Founder, 2026-10-11, on a chat reply where the people rendered as chips and the meeting rendered as
`2026-10-09-1401-zoom-meeting-88410021706.md`: *"i think we need a special label for a meeting
alongside people companies etc"*.

A meeting's notes page is a file on a desk (`meeting_note.MEETING_DIR`), and its filename is a
stamp plus a slug: the right name for a file and the wrong one for a meeting. The meeting's name is
the row's `data.title` — what a rename writes — and its time is the row's. So a link that names the
notes page is answered with the meeting behind it, read off the page's own frontmatter `meeting:`
(the row id every writer stamps: `shared/meeting_doc.scaffold`, the flow's drop).

THE ROW IS READ THROUGH THE ONE MEETING ACCESS DECISION (`peer_lookups.meeting_access_check`):
owner, transcript-share recipient, or member of the meeting's workspace. A reader who may open the
page but not the meeting gets `not-yours` and NO title: the title is the meeting's, and it is
answered only to somebody the meeting is answered to. The page itself must be on a tree this reader
may read (`here`, already access-checked by the caller, then their own desk); a page found nowhere
is `gone`. Neither state carries a word of the meeting.

Pure over a workspace tree plus an injected access callable, like `link_resolver`, so every state
is provable offline.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, Optional

from control_plane import meeting_note
from control_plane import workspace_ids as ids_mod
from workspaces.shared import workspace_paths as wpaths

#: The notes page inside any spelling a writer uses: workspace-relative, `./`-prefixed, or the
#: worker's absolute mount path (`/workspaces/<slug>/kg/entities/meeting/<name>.md`).
_NOTE_TAIL = re.compile(r"(?:\A|/)(" + re.escape(meeting_note.MEETING_DIR) + r"/[^/]+)\Z")

#: Frontmatter is at the head of the page; the report below it is not needed to answer.
_HEAD_BYTES = 4096

KIND = "meeting"


def note_path_of(ref: str) -> str:
    """The `kg/entities/meeting/<name>.md` this ref names, or `""` when it names no notes page.

    The shape check is `meeting_note.is_note_path` — the alphabet the minter writes — so a ref with
    `..`, a nested folder or the directory's `index.md` is not a meeting link."""
    s = str(ref or "").strip()
    s = re.sub(r"[?#].*\Z", "", s)
    m = _NOTE_TAIL.search(s)
    tail = m.group(1) if m else ""
    return tail if meeting_note.is_note_path(tail) else ""


def is_meeting_ref(ref: str) -> bool:
    return bool(note_path_of(ref))


def _when(row: dict) -> str:
    """When the meeting is: it ran, else it was due, else it was requested — meeting-api's own
    ordering, COALESCE(start_time, scheduled_at, created_at), started first."""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    for v in (row.get("start_time"), (data or {}).get("scheduled_at"), row.get("created_at")):
        if str(v or "").strip():
            return str(v).strip()
    return ""


def _answer(ref: str, access: str) -> dict:
    return {"ref": ref, "kind": KIND, "title": "", "url": None, "access": access,
            "workspace": None, "writable": False}


def resolve(ref: str, *, roots: Iterable[Path],
            access: Callable[[str], Optional[dict]]) -> Optional[dict]:
    """One ref → the meeting it names, or ``None`` when the ref is not a meeting link at all.

    ``roots`` are the trees this reader may read, in order (the caller checks them). ``access`` is
    ``meeting_id -> row | None`` under the reader's identity."""
    note = note_path_of(ref)
    if not note:
        return None
    head = None
    for root in roots:
        if root is None:
            continue
        try:
            if wpaths.is_file_inside(Path(root), note):
                head = wpaths.read_head_inside(Path(root), note, _HEAD_BYTES)
        except (OSError, ValueError):
            head = None
        if head is not None:
            break
    if head is None:
        return _answer(ref, ids_mod.ACCESS_GONE)
    fm = meeting_note.front_matter(head)
    meeting_id = str(fm.get("meeting") or "").strip()
    if not meeting_id.isdigit():
        # A page with no meeting behind it — a report written before the stamp existed. The reader
        # may read the PAGE, so the page's own title and date are theirs; there is no row to check.
        out = _answer(ref, ids_mod.ACCESS_READABLE)
        out.update({"title": str(fm.get("title") or "").strip(), "at": str(fm.get("date") or "").strip(),
                    "path": note, "meeting": None})
        return out
    try:
        row = access(meeting_id)
    except Exception:  # noqa: BLE001 — an access check that failed is a refusal, never an opening
        row = None
    if not isinstance(row, dict):
        return _answer(ref, ids_mod.ACCESS_NOT_YOURS)
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    platform = str(row.get("platform") or "").strip()
    native = str(row.get("native_meeting_id") or "").strip()
    out = _answer(ref, ids_mod.ACCESS_READABLE)
    out.update({
        # The meeting's OWN name — a rename writes `data.title` — never the page's or the file's.
        # Empty when nobody named it: the client says platform · date, which is what it is.
        "title": str((data or {}).get("title") or "").strip(),
        "at": _when(row),
        "platform": platform,
        "path": note,
        "meeting": {"id": str(row.get("id") or meeting_id), "platform": platform, "native": native},
    })
    return out
