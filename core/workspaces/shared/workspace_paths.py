"""workspace_paths.py — ONE answer to "does this caller-supplied path stay inside this workspace".

Before this module the answer was spelled six times across five files and the spellings disagreed.
``link_resolver.escapes`` counted ``..`` segments and nothing else — and ``Path("/ws") / "/etc/passwd"``
is ``/etc/passwd``, because an ABSOLUTE path silently DISCARDS the root it was joined to. So
``[[ws:abcdefghij//etc/passwd]]`` walked past the guard, the resolver read the file and echoed
``path`` and ``url`` back to the client, which then handed both to the file endpoint (R-A06,
reachable on ``POST /api/links/resolve`` — the read path for any document a person opens).

The other five spellings each caught a different subset. That is the failure this module exists to
end: **a path from outside is refused for four reasons and every route gets all four.**

* **absolute** — the root is dropped, as above;
* **``..``** — the ordinary traversal, including one that only escapes after descending first;
* **a symlink out** — the string stays inside and the RESOLVED path does not, which is the shape no
  purely textual check can ever see;
* **a reserved directory** — ``.git`` (the repository's own store: history, and hooks that execute
  on the next commit) and ``.vexa`` (the workspace identity ``shared/workspace_id.py`` writes —
  overwrite it and the workspace mints a new id, so every ``[[ws:<id>/…]]`` link into it resolves
  ``gone``, PRD decision 26.1). A route that legitimately owns one names it in ``allow``.

Refusal is an EXCEPTION, never a ``None`` or a ``False``: of the six spellings this replaces, the
ones that returned a value each had at least one caller that did not look at it.
"""
from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path
from typing import Optional

#: Directories no caller-supplied path may reach into. ``allow=(".git",)`` opens one for a route
#: that owns it — nothing does today; the parameter exists so a future one need not weaken the rule.
RESERVED_DIRS = (".git", ".vexa")


class PathRefused(ValueError):
    """A caller-supplied path that does not stay inside the workspace it names.

    ``kind`` says which of the four rules refused it — for a log line, never for the caller's
    response body, where one sentence for all four is the honest answer (a probe must not learn
    from the refusal WHY it was refused)."""

    def __init__(self, reason: str, *, kind: str) -> None:
        super().__init__(reason)
        self.kind = kind      # 'absolute' | 'traversal' | 'reserved' | 'symlink' | 'empty'


REFUSAL = "that path is not inside this workspace"


def relative_parts(path: str, *, allow=()) -> list[str]:
    """The path's segments, or ``PathRefused`` — the TEXTUAL half of the rule.

    Split out because two callers have no root to resolve against: ``link_resolver.escapes`` answers
    about a link before it knows which workspace it lands in, and ``desk_touch`` records a path it
    never opens. Both still get absolute · ``..`` · reserved."""
    rel = str(path or "").strip().replace("\\", "/")
    if not rel:
        raise PathRefused(REFUSAL, kind="empty")
    if rel.startswith("/") or Path(rel).is_absolute():
        raise PathRefused(REFUSAL, kind="absolute")
    parts = [p for p in rel.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise PathRefused(REFUSAL, kind="traversal")
    if not parts:
        raise PathRefused(REFUSAL, kind="empty")
    reserved = {d for d in RESERVED_DIRS if d not in set(allow or ())}
    if any(p in reserved for p in parts):
        raise PathRefused(REFUSAL, kind="reserved")
    return parts


def resolve_inside(root, path: str, *, allow=()) -> Path:
    """The absolute path ``path`` names INSIDE ``root``, or ``PathRefused``.

    ``root`` is resolved once and every comparison is made against the resolved value, so a store
    reached through a symlink is not itself an escape — only a link that leaves the workspace is."""
    parts = relative_parts(path, allow=allow)
    base = Path(root).resolve()
    target = (base / "/".join(parts)).resolve()
    if target != base and base not in target.parents:
        raise PathRefused(REFUSAL, kind="symlink")
    return target


def is_inside(root, path: str, *, allow=()) -> bool:
    """``resolve_inside`` as a predicate — for the read paths whose answer is "not found", not 400."""
    try:
        resolve_inside(root, path, allow=allow)
    except (PathRefused, OSError):
        return False
    return True


# ── reaching a FIXED platform file inside a work tree, without following a link ──────────────────
#
# ``resolve_inside`` above answers about a CALLER-supplied path and refuses one that leaves the
# workspace. These helpers are for the other half: a path the PLATFORM fixes (``PURPOSE``,
# ``.claude/mcp.json``, ``kg/entities/<kind>/<slug>.md``, ``.vexa/workspace.json``) that agent-api or
# the worker — running as root — reads or writes inside a work tree the model's tools can also write
# during a turn. A plain ``open``/``read_text``/``write_text`` follows a symlink the tools user plants
# at the file or at any directory above it, so the root process can be redirected to read another
# tenant's file (its content then returned to a caller or folded into the model's prompt) or to write
# through the link (a credential or an identity landing wherever the link points). So each directory
# component is opened ``O_NOFOLLOW`` from its parent's descriptor, the file is read only when it is a
# regular file with a single hard link, and a write goes to a new ``O_EXCL`` file renamed into place.
# ``rel`` is still split through ``relative_parts`` (absolute / ``..`` / a reserved dir refused unless
# ``allow`` names it), because a fixed path is no reason to skip the textual rule.
_DIR_NOFOLLOW = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE_NOFOLLOW = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_CREATE_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_root(root) -> int:
    """A descriptor for the mount ``root`` itself (opened as given — it is the platform's trusted
    base, resolved by the dispatcher). Raises ``OSError`` if it is not a directory."""
    return os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))


def dir_fd_inside(root, parts, *, create: bool = False, mode: int = 0o755) -> int:
    """A descriptor for ``root/<parts…>``, every component below ``root`` opened without following a
    link; a symlink or non-directory on the way raises :class:`PathRefused` (``kind='symlink'``). With
    ``create`` a missing component is made (0o755) first — ``root`` itself included, so a write to a
    workspace the seed has not materialized yet still lands. The caller closes the descriptor."""
    if create:
        Path(root).mkdir(parents=True, exist_ok=True)
    fd = _open_root(root)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                except FileExistsError:
                    pass                 # already a real directory → the O_NOFOLLOW open confirms it
                except OSError as exc:
                    # a symlink (EEXIST is FileExistsError; a symlink-to-dir gives ENOTDIR/ELOOP here)
                    raise PathRefused(REFUSAL, kind="symlink") from exc
            try:
                nxt = os.open(part, _DIR_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                raise PathRefused(REFUSAL, kind="symlink") from exc
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_text_inside(root, rel: str, *, max_bytes: Optional[int] = None, allow=()) -> Optional[str]:
    """The UTF-8 text of the FIXED path ``root/<rel>`` read without following a link anywhere below
    ``root``; ``None`` when it is missing, reached through a link, not a regular file with a single
    hard link, larger than ``max_bytes``, or not valid UTF-8."""
    try:
        parts = relative_parts(rel, allow=allow)
    except PathRefused:
        return None
    *dirs, name = parts
    try:
        dir_fd = dir_fd_inside(root, dirs)
    except (PathRefused, OSError):
        return None
    try:
        fd = os.open(name, _FILE_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        return None
    finally:
        os.close(dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            return None
        if max_bytes is not None and st.st_size > max_bytes:
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            raw = fh.read()
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_bytes_inside(root, rel: str, *, max_bytes: Optional[int] = None, allow=()) -> Optional[bytes]:
    """Like :func:`read_text_inside` but the raw bytes (for a non-text asset); ``None`` on the same
    refusals, and when it is larger than ``max_bytes``."""
    try:
        parts = relative_parts(rel, allow=allow)
    except PathRefused:
        return None
    *dirs, name = parts
    try:
        dir_fd = dir_fd_inside(root, dirs)
    except (PathRefused, OSError):
        return None
    try:
        fd = os.open(name, _FILE_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        return None
    finally:
        os.close(dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            return None
        if max_bytes is not None and st.st_size > max_bytes:
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            return fh.read()
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def write_text_inside(root, rel: str, text: str, *, mode: int = 0o644, allow=(),
                      make_parents: bool = True, before_replace=None) -> Path:
    """Write ``text`` to the FIXED path ``root/<rel>``, creating a NEW file and renaming it into
    place so a symlink already at the name is replaced rather than written through, and with no
    directory component on the path followed through a link. Raises :class:`PathRefused` when a
    component is a link (or ``rel`` is absolute / escapes / names a reserved dir not in ``allow``).

    ``before_replace``, when given, is called with the new file's own descriptor after it is written
    and before it is renamed into place — e.g. to ``fchown`` a credential to the user that will read
    it, so it is never visible at its name owned by anyone else. The new file's name is random and
    created ``O_EXCL``, so nothing at a predictable temp name is ever written through. Returns the
    file's path."""
    parts = relative_parts(rel, allow=allow)
    *dirs, name = parts
    dir_fd = dir_fd_inside(root, dirs, create=make_parents)
    try:
        tmp = f".{name}.{os.getpid()}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
        fd = os.open(tmp, _CREATE_NEW, mode, dir_fd=dir_fd)
        try:
            try:
                os.fchmod(fd, mode)      # exact mode regardless of umask (a credential may be 0o600)
                view = memoryview(text.encode("utf-8"))
                while view:
                    view = view[os.write(fd, view):]
                if before_replace is not None:
                    before_replace(fd)
            finally:
                os.close(fd)
            os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except OSError:
                pass
            raise
    finally:
        os.close(dir_fd)
    return Path(root) / "/".join(parts)


def unlink_inside(root, rel: str, *, allow=()) -> None:
    """Remove ``root/<rel>`` (a symlink is removed itself, never followed) with no directory
    component on the path followed through a link. A link or missing directory on the way, or a
    missing leaf, is a silent no-op; the leaf is removed only when it is a symlink or a regular
    file (never a directory)."""
    try:
        parts = relative_parts(rel, allow=allow)
    except PathRefused:
        return
    *dirs, name = parts
    try:
        dir_fd = dir_fd_inside(root, dirs)
    except (PathRefused, OSError):
        return
    try:
        st = os.lstat(name, dir_fd=dir_fd)
        if stat.S_ISLNK(st.st_mode) or stat.S_ISREG(st.st_mode):
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                pass
    except OSError:
        pass
    finally:
        os.close(dir_fd)
