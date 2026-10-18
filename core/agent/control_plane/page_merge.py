"""TWO WRITERS ON ONE PAGE NEVER LOSE A LINE (#1783, collaboration rehearsal 2026-10-14).

A page write replaces the whole file. Two agents working one shared workspace during a meeting each
read `notes.md`, add their bullet, and write the page back; the second write silently erased the
first one's bullet. Neither agent passes the version it read (one reads through the mount, not the
tool), so the base is recovered from the page's own recent history: the version the incoming text
is closest to. When that is not the current head, somebody else wrote in between, and the two
changes are merged three ways:

  * clean merge → the merged page is written, and the answer says it was merged;
  * both sides only ADDED lines at the same place (two bullets after the same line) → both kept,
    the other writer's lines first;
  * both changed the same lines → refused (409) with the page as it is now, so the writer re-reads
    and applies its change again. Nothing is written, nothing is lost.

A writer whose text derives from the current head (the normal, single-writer case) is untouched.
The whole read-merge-write-commit runs under a per-workspace file lock, so two writes landing in
the same instant are serialised rather than interleaved.
"""
from __future__ import annotations

import contextlib
import difflib
import fcntl
import os
import time
from pathlib import Path
from typing import Iterator, Optional

from shared.gitexec import run_git
from workspaces.shared import workspace_paths as wpaths

#: How far back a write may have started from. A turn reads and writes within a minute or two; a
#: page last changed longer ago than this is not "changed while you were editing".
WINDOW_SEC = 900
#: How many recent versions of the page are considered as the base a writer started from.
MAX_VERSIONS = 12


class PageConflict(Exception):
    """Both writers changed the same lines. ``current`` is the page as it is now."""

    def __init__(self, current: str):
        super().__init__("page changed by someone else on the same lines")
        self.current = current


@contextlib.contextmanager
def write_lock(target: Path) -> Iterator[None]:
    """Serialise page writes in one workspace (across agent-api worker processes on this host)."""
    gitdir = Path(target) / ".git"
    lock_dir = gitdir if gitdir.is_dir() else Path(target)
    path = lock_dir / "vexa-page-write.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _recent_versions(target: Path, rel: str, now: float) -> list[str]:
    """The page's committed versions changed within the window, newest first, plus the version just
    before the window opened (the base an edit that started then would have read)."""
    log = run_git(target, "log", f"-n{MAX_VERSIONS}", "--format=%H %ct", "--", rel)
    if log.returncode != 0:
        return []
    out: list[str] = []
    for line in (log.stdout or "").splitlines():
        try:
            sha, ct = line.split()
        except ValueError:
            continue
        show = run_git(target, "show", f"{sha}:{rel}")
        if show.returncode == 0:
            out.append(show.stdout)
        if now - int(ct) > WINDOW_SEC:
            break  # this one predates the window: it is the oldest base worth considering
    return out


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False).ratio()


#: A version is a plausible base only when the incoming text still carries most of it: an edit
#: keeps what it did not touch. A rewrite carries little of any version and is taken as written.
KEPT_MIN = 0.8


def _kept(base: str, content: str) -> float:
    b = base.splitlines()
    if not b:
        return 0.0
    m = difflib.SequenceMatcher(None, b, content.splitlines(), autojunk=False)
    return sum(blk.size for blk in m.get_matching_blocks()) / len(b)


def _changes(base: list[str], side: list[str]) -> list[tuple[int, int, list[str]]]:
    """``(base_start, base_end, replacement)`` for every region ``side`` changed in ``base``."""
    sm = difflib.SequenceMatcher(None, base, side, autojunk=False)
    return [(i1, i2, side[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _merge3(ours: str, base: str, theirs: str) -> str:
    """Three-way merge by lines, in memory. Changes in different places are both applied; two pure
    insertions at the same place are both kept (theirs first); the same change on both sides is kept
    once; anything else touching the same lines raises :class:`PageConflict`."""
    b = base.splitlines(keepends=True)
    o = ours.splitlines(keepends=True)
    t = theirs.splitlines(keepends=True)
    for seq in (b, o, t):
        if seq and not seq[-1].endswith("\n"):
            seq[-1] += "\n"
    edits = sorted([(s, e, r, "t") for s, e, r in _changes(b, t)] + [(s, e, r, "o") for s, e, r in _changes(b, o)],
                   key=lambda x: (x[0], x[1], x[3] != "t"))
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(edits):
        s, e, r, who = edits[i]
        group = [edits[i]]
        hi = e
        j = i + 1
        # gather every edit overlapping this one's base range (an insertion at the same point counts)
        while j < len(edits):
            s2, e2, _r2, _w2 = edits[j]
            if s2 < hi or (s2 == s and e2 == s and e == s) or (s2 == hi and e2 == s2 and hi == s):
                group.append(edits[j])
                hi = max(hi, e2)
                j += 1
            else:
                break
        out.extend(b[pos:s])
        sides = {g[3] for g in group}
        if len(sides) == 1:
            # one writer's own consecutive edits
            cur = s
            for gs, ge, gr, _ in group:
                out.extend(b[cur:gs])
                out.extend(gr)
                cur = ge
            out.extend(b[cur:hi])
        else:
            ts = [g for g in group if g[3] == "t"]
            os_ = [g for g in group if g[3] == "o"]
            if all(g[0] == g[1] for g in group):
                out.extend(x for g in ts for x in g[2])
                seen = {x for g in ts for x in g[2]}
                out.extend(x for g in os_ for x in g[2] if x not in seen)
            elif [(g[0], g[1], g[2]) for g in ts] == [(g[0], g[1], g[2]) for g in os_]:
                for g in ts:
                    out.extend(g[2])
            else:
                raise PageConflict(theirs)
        pos = hi
        i = j
    out.extend(b[pos:])
    return "".join(out)


def reconcile(target: Path, rel: str, content: str, *, now: Optional[float] = None) -> tuple[str, bool]:
    """``(what to write, merged?)`` for a whole-page write of ``content`` to ``rel`` in ``target``.

    Call under :func:`write_lock`. Raises :class:`PageConflict` when the writer's change and a
    change made since it read the page touch the same lines."""
    target = Path(target)
    try:
        fbase, frel = wpaths.locate_inside(target, rel)
        head = wpaths.read_text_inside(fbase, frel)
    except wpaths.PathRefused:
        return content, False
    if head is None:
        return content, False
    if head == content or not (target / ".git").is_dir():
        return content, False
    versions = _recent_versions(target, rel, time.time() if now is None else now)
    if not versions or versions[0] != head:
        # the file on disk is not what was last committed (an edit outside the page verbs): the
        # history cannot say what anyone read, so this write is taken as it is.
        return content, False
    if content in versions[1:]:
        # exactly an earlier version: an undo, meant as written
        return content, False
    best, best_score = None, _similar(content, head)
    for v in versions[1:]:
        if v == head:
            continue
        s = _similar(content, v)
        if s > best_score and _kept(v, content) >= KEPT_MIN:
            best, best_score = v, s
    if best is None:
        return content, False
    merged = _merge3(content, best, head)
    if merged == head:
        return head, True
    return merged, True
