"""git_forget.py — take something out of a workspace's git history, not just its tree (Vexa-ai/vexa#1783).

Deleting a page is a commit; the page is still in the history, and anything that can read the
history can read it. A forget has to rewrite: every commit that carried the item is replaced by one
that does not, the old objects are expired and pruned, and the working tree is left matching.

No `git filter-repo` in the image, so this is the same idea with git's own two halves:
`git fast-export` the whole history as a stream, edit the stream, `git fast-import --force` it
back. The edit is two things:

* **paths** — a file-change line for a path being forgotten (the path itself, or anything under a
  folder being forgotten) is dropped, and the blob it pointed at is dropped with it when nothing
  else uses that blob;
* **text** — inside every blob and every commit message, each occurrence of a literal pattern
  (case-insensitive) and, when asked, every identifier ``sensitive.redact`` finds, is replaced with
  ``[removed]``.

The answer counts what changed — files, commits, matches — and never carries a removed value.
Every git call goes through ``shared.gitexec.run_git``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from shared import sensitive
from shared.gitexec import run_git
from workspaces.shared import workspace_paths as wpaths

REMOVED = sensitive.REMOVED
#: A workspace bigger than this is not rewritten in memory; the person is told, nothing changes.
MAX_STREAM_BYTES = 512 * 1024 * 1024


class ForgetRefused(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class Outcome:
    files_removed: set = field(default_factory=set)      # paths dropped from history or tree
    commits_changed: int = 0
    matches_redacted: int = 0
    blobs_dropped: int = 0

    def as_dict(self) -> dict:
        return {"files_removed": sorted(self.files_removed), "commits_rewritten": self.commits_changed,
                "matches_redacted": self.matches_redacted}


def _git(ws: Path, *args: str, check: bool = True, text: bool = True, input=None, timeout: float = 300):
    return run_git(ws, *args, check=check, text=text, input=input, timeout=timeout)


def _unquote(p: bytes) -> str:
    s = p.decode("utf-8", "surrogateescape")
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        body = s[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
        return body.encode("latin-1", "surrogateescape").decode("utf-8", "replace")
    return s


def matches_path(path: str, targets: list[str]) -> bool:
    for t in targets:
        t = t.strip().strip("/")
        if t and (path == t or path.startswith(t + "/")):
            return True
    return False


class _Redactor:
    def __init__(self, pattern: Optional[str], identifiers: bool, literals=()):
        self.run = sensitive.redactor(literals=literals, pattern=pattern or "", identifiers=identifiers)

    def __bool__(self):
        return bool(self.run.active)

    def text(self, s: str) -> tuple[str, int]:
        return self.run(s)

    def data(self, raw: bytes) -> tuple[bytes, int]:
        if not self:
            return raw, 0
        if b"\0" in raw[:8000]:
            return raw, 0                     # binary: only a path forget removes it
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw, 0
        out, n = self.text(s)
        return (out.encode("utf-8"), n) if n else (raw, 0)


def _parse(stream: bytes) -> list[dict]:
    """The fast-export stream as a list of commands: ``{"head": line, "lines": [...], "data": bytes|None}``
    with the order of lines and data kept (a data block sits where it was)."""
    cmds: list[dict] = []
    i, n = 0, len(stream)
    cur: Optional[dict] = None
    while i < n:
        j = stream.find(b"\n", i)
        if j < 0:
            j = n
        line = stream[i:j]
        i = j + 1
        if line.startswith(b"data "):
            size = int(line[5:])
            body = stream[i:i + size]
            i += size
            if i < n and stream[i:i + 1] == b"\n":
                i += 1
            if cur is not None:
                cur["parts"].append(("data", body))
            continue
        if line and not line.startswith((b" ", b"M ", b"D ", b"R ", b"C ", b"N ", b"from ", b"merge ",
                                         b"mark ", b"author ", b"committer ", b"tagger ", b"encoding ",
                                         b"original-oid ", b"deleteall")) and cur is not None \
                and line.split(b" ", 1)[0] in (b"blob", b"commit", b"reset", b"tag", b"done",
                                               b"feature", b"option", b"progress", b"checkpoint"):
            cur = None
        if not line:
            continue
        if cur is None:
            cur = {"kind": line.split(b" ", 1)[0], "parts": [("line", line)]}
            cmds.append(cur)
        else:
            cur["parts"].append(("line", line))
    return cmds


def _mark(cmd: dict) -> Optional[bytes]:
    for k, v in cmd["parts"]:
        if k == "line" and v.startswith(b"mark "):
            return v[5:].strip()
    return None


def _emit(cmds: list[dict]) -> bytes:
    out = bytearray()
    for c in cmds:
        for k, v in c["parts"]:
            if k == "line":
                out += v + b"\n"
            else:
                out += b"data %d\n" % len(v) + v
        if c["kind"] in (b"blob", b"commit", b"tag"):
            out += b"\n"
    return bytes(out)


def rewrite(ws: Path, *, paths: list[str], pattern: Optional[str], identifiers: bool,
            outcome: Outcome, literals=()) -> None:
    """Rewrite every ref's history in ``ws`` (see the module header)."""
    exported = _git(ws, "fast-export", "--all", "--reencode=yes", "--signed-tags=strip",
                    "--tag-of-filtered-object=drop", text=False).stdout
    if len(exported) > MAX_STREAM_BYTES:
        raise ForgetRefused("this workspace's history is too large to rewrite here", status=413)
    cmds = _parse(exported)
    red = _Redactor(pattern, identifiers, literals)
    blobs: dict[bytes, dict] = {}
    used: dict[bytes, int] = {}
    redacted: set = set()
    for c in cmds:
        if c["kind"] == b"blob":
            m = _mark(c)
            if m:
                blobs[m] = c
            parts = []
            for k, v in c["parts"]:
                if k == "data":
                    nv, n = red.data(v)
                    if n:
                        outcome.matches_redacted += n
                        redacted.add(m)
                        v = nv
                parts.append((k, v))
            c["parts"] = parts
    for c in cmds:
        if c["kind"] != b"commit":
            continue
        changed = False
        kept = []
        for k, v in c["parts"]:
            if k == "line" and v[:2] in (b"M ", b"D "):
                if v.startswith(b"M "):
                    _, _mode, ref, p = v.split(b" ", 3)
                else:
                    ref, p = None, v[2:]
                if matches_path(_unquote(p), paths):
                    outcome.files_removed.add(_unquote(p))
                    changed = True
                    continue
                if ref is not None and ref.startswith(b":"):
                    used[ref] = used.get(ref, 0) + 1
                    if ref in redacted:
                        changed = True
            elif k == "data":
                nv, n = red.data(v)
                if n:
                    outcome.matches_redacted += n
                    changed = True
                    v = nv
            kept.append((k, v))
        c["parts"] = kept
        if changed:
            outcome.commits_changed += 1
    out_cmds = []
    for c in cmds:
        if c["kind"] == b"blob":
            m = _mark(c)
            if m and m not in used:
                outcome.blobs_dropped += 1
                continue
        if c["kind"] == b"commit":
            c["parts"] = [(k, v) for k, v in c["parts"] if not (k == "line" and v.startswith(b"original-oid "))]
        if c["kind"] == b"blob":
            c["parts"] = [(k, v) for k, v in c["parts"] if not (k == "line" and v.startswith(b"original-oid "))]
        out_cmds.append(c)
    stream = _emit(out_cmds)
    _git(ws, "fast-import", "--force", "--quiet", input=stream, text=False)


def expire(ws: Path) -> None:
    """Make the old objects unreachable and gone: reflogs expired, everything unreferenced pruned."""
    for ref in ("ORIG_HEAD", "FETCH_HEAD"):
        _git(ws, "update-ref", "-d", ref, check=False)
    _git(ws, "reflog", "expire", "--expire=now", "--all", check=False)
    _git(ws, "gc", "--prune=now", "--quiet", check=False)


def contains(ws: Path, *, paths: list[str], pattern: Optional[str]) -> bool:
    """Does any object reachable from any ref still carry the item? (The test a forget is held to.)"""
    if paths:
        listed = _git(ws, "log", "--all", "--name-only", "--format=", check=False).stdout
        if any(matches_path(p.strip(), paths) for p in listed.splitlines() if p.strip()):
            return True
    if pattern and pattern.strip():
        r = _git(ws, "grep", "-i", "-F", "-l", "-e", pattern,
                 *[x for x in _git(ws, "rev-list", "--all", check=False).stdout.split()], check=False)
        if r.returncode == 0 and r.stdout.strip():
            return True
        msgs = _git(ws, "log", "--all", "--format=%B", check=False).stdout
        if pattern.lower() in msgs.lower():
            return True
    return False


def forget(ws: Path, *, paths: list[str], pattern: Optional[str], identifiers: bool,
           author: tuple[str, str] = ("Vexa", "platform@vexa.ai"), literals=()) -> Outcome:
    """The whole act on one workspace repo: pending changes are committed first (so nothing in the
    tree is lost by the rewrite), history is rewritten, the tree is reset to it, the forgotten paths
    are removed from disk even where git never tracked them (the attachment drawer), and the old
    objects are pruned. Raises ``ForgetRefused`` when the item survives anywhere a ref reaches."""
    ws = Path(ws)
    out = Outcome()
    if (ws / ".git").is_dir():
        if _git(ws, "status", "--porcelain", check=False).stdout.strip():
            _git(ws, "-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}", "add", "-A")
            # never what the turn commit never takes: runtime plumbing and the attachment drawer
            _git(ws, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", ".claude", "uploads",
                 check=False)
            _git(ws, "-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}",
                 "commit", "-q", "--no-verify", "-m", "pending changes, kept before a forget", check=False)
        has_head = _git(ws, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0
        if has_head:
            rewrite(ws, paths=paths, pattern=pattern, identifiers=identifiers, outcome=out,
                    literals=literals)
            _git(ws, "reset", "-q", "--hard", "HEAD")
    for p in paths:
        rel = p.strip().strip("/")
        try:
            if wpaths.unlink_inside(ws, rel):
                out.files_removed.add(rel)
            elif wpaths.list_files_inside(ws, rel) or (ws / rel).is_dir():
                wpaths.remove_tree(ws / rel)
                out.files_removed.add(rel)
        except (wpaths.PathRefused, OSError):
            continue
    if (ws / ".git").is_dir():
        expire(ws)
        if contains(ws, paths=paths, pattern=pattern):
            raise ForgetRefused("the item is still reachable in this workspace's history", status=500)
    return out
