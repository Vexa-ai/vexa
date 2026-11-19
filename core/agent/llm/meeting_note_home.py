"""ONE NOTE PER MEETING, enforced at the commit seam (Vexa-ai/vexa#1783).

A meeting's note has ONE home: ``<root>/<subject>/kg/entities/meeting/<name>.md`` — where the bot
send mints it, where ``/api/meeting/note`` reads it for the meeting view, and where the post-meeting
report is dropped. A turn in a chat that works in another workspace can still write the same
relative path with its own file tools into ITS working tree (the founder's call on 2026-10-14: the
agent "extended" the note four times into an attached workspace while the meeting view kept showing
the empty one). The page-write route already sends such a write home; a native file write never
passes it, so the turn's commit — the one place every write crosses — does the same:

  * a meeting note (``type: meeting``) this turn changed in a mount that is NOT the home, at a path
    the home holds as a meeting note, is folded INTO the home's note, and
  * the stray copy is removed from that mount, so the change is committed once, where the meeting
    view reads it.

WHY REDIRECT, NOT MOVE THE BINDING. Three writers and one reader already agree on the home (the mint,
the post-meeting drop, the Update-notes ask; the meeting view). Moving the binding to wherever the
last chat wrote would make the note's home depend on which conversation touched it last and would
have to be re-taught to all four. Redirecting keeps one fixed home and costs nothing to anyone.

THE FOLD. A home note with nothing written in it yet (frontmatter, the transcript marker, empty
regions) takes the turn's copy whole. Otherwise a three-way merge — base: the stray copy as it was
before the turn (empty if the turn created it), ours: the home note, theirs: the turn's copy — keeps
both sides' lines and lets the turn's write win where the same lines changed.

Stdlib + ``llm`` only, like the rest of the commit seam.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from llm import workspace_paths as wpaths
from llm.gitexec import run_git

MEETING_DIR = "kg/entities/meeting"
_NOTE = re.compile(r"^kg/entities/meeting/[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md$")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_MAX = 2_000_000


def is_note_path(rel: str) -> bool:
    return bool(_NOTE.match(rel)) and not rel.endswith("/index.md")


def is_meeting_note(text: Optional[str]) -> bool:
    m = _FRONTMATTER.match(text or "")
    if not m:
        return False
    for ln in m.group(1).splitlines():
        k, sep, v = ln.partition(":")
        if sep and k.strip() == "type":
            return v.split("#")[0].strip().strip("'\"") == "meeting"
    return False


def nothing_written(text: str) -> bool:
    """A note with no words in it yet: frontmatter, HTML comments (the transcript marker, region
    fences) and headings only."""
    body = _FRONTMATTER.sub("", text or "", count=1)
    body = re.sub(r"<!--[\s\S]*?-->", "", body)
    body = re.sub(r"(?m)^#{1,6}\s.*$", "", body)
    return not body.strip()


def home_of(owner: str, mounts: Iterable[Path]) -> Optional[Path]:
    """The mount at ``<root>/<owner>`` — the seed slot `/api/meeting/note` reads — or None when this
    turn does not mount it. An attached workspace lives at ``<root>/.attached/<owner>/<slug>``, so a
    mount named like the owner under a parent of the same name is not it."""
    o = str(owner or "").strip()
    if not o:
        return None
    for m in mounts:
        p = Path(m)
        if p.name == o and p.parent.name != o:
            return p
    return None


def _git_out(repo: Path, *args: str) -> str:
    return run_git(repo, *args, check=True).stdout


def _changed_notes(mount: Path) -> list[str]:
    """Meeting-note paths this turn added or modified in ``mount`` (working tree vs HEAD)."""
    try:
        raw = _git_out(mount, "status", "--porcelain", "-z", "--untracked-files=all", "--", MEETING_DIR)
    except Exception:  # noqa: BLE001 — a mount we cannot read is left to its own commit
        return []
    out: list[str] = []
    for entry in raw.split("\0"):
        if len(entry) < 4:
            continue
        code, rel = entry[:2], entry[3:]
        if "D" in code:
            continue
        if is_note_path(rel):
            out.append(rel)
    return out


def _head_text(mount: Path, rel: str) -> str:
    try:
        return run_git(mount, "show", f"HEAD:{rel}", check=True).stdout
    except Exception:  # noqa: BLE001 — not at HEAD: the turn created it
        return ""


def merge3(base: str, ours: str, theirs: str) -> str:
    """``git merge-file`` over three texts; on a conflict the turn's lines (theirs) win."""
    with tempfile.TemporaryDirectory(prefix="vx-note-") as d:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            wpaths.write_text_inside(d, name, text)
            paths.append(os.path.join(d, name))
        r = run_git(d, "merge-file", "-p", "--theirs", *paths)
        if r.returncode < 0 or (r.returncode > 0 and not r.stdout):
            return theirs
        return r.stdout


def fold(home_text: str, base: str, turn_text: str) -> str:
    if nothing_written(home_text):
        return turn_text
    return merge3(base, home_text, turn_text)


def redirect_stray_notes(home: Optional[Path], mounts: Iterable[Path]) -> list[dict]:
    """Fold every stray meeting note this turn wrote outside ``home`` into the home's note, and
    remove the stray copy. Returns one ``{"from", "path"}`` per note moved. Never raises."""
    if home is None:
        return []
    home_key = str(Path(home).resolve())
    moved: list[dict] = []
    for mount in mounts:
        mount = Path(mount)
        if str(mount.resolve()) == home_key:
            continue
        for rel in _changed_notes(mount):
            try:
                turn_text = wpaths.read_text_inside(mount, rel, max_bytes=_MAX)
                home_text = wpaths.read_text_inside(home, rel, max_bytes=_MAX)
            except Exception:  # noqa: BLE001
                continue
            if turn_text is None or not is_meeting_note(turn_text):
                continue
            if home_text is None or not is_meeting_note(home_text):
                continue   # the home holds no note at this path: an ordinary page in that workspace
            try:
                merged = fold(home_text, _head_text(mount, rel), turn_text)
                wpaths.write_text_inside(home, rel, merged)
                wpaths.unlink_inside(mount, rel)
            except Exception:  # noqa: BLE001 — a note we could not move is committed where it is
                continue
            moved.append({"from": mount.name, "path": rel})
    return moved
