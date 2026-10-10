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

The second half of this module (``read_text_inside`` and its siblings, below) is the other side of
the same rule: a FIXED platform path read or written by agent-api or the worker inside a work tree,
never through a link planted at the file or at any directory above it.

The canonical copy is ``core/workspaces/shared/workspace_paths.py``. It is vendored VERBATIM into
``core/agent/llm/workspace_paths.py`` (the worker's ``llm`` package imports no product code); the
parity fact ``workspace-paths`` in ``scripts/parity.json`` holds the copies byte-identical, and
``core/agent/tests/test_worktree_io_single_path.py`` fails on a bare file call outside it that is not
allow-listed with a reason. Standard library only.
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


def locate_inside(root, path: str, *, allow=()) -> "tuple[Path, str]":
    """``(root resolved, the RESOLVED target's path relative to it)`` — :func:`resolve_inside`'s
    check, in the shape the no-follow helpers below act on (``""`` when ``path`` names the root).

    CHECK ONCE, ACT ON WHAT WAS CHECKED. Resolving and then opening by name leaves a window in which
    a link swapped in sends the act somewhere else. Acting through ``*_inside(root, rel)`` on the
    resolved, already link-free ``rel`` opens every component ``O_NOFOLLOW``, so a link that appears
    after this check refuses the act instead of redirecting it — while a link that stays INSIDE the
    workspace still works, because it is resolved here, before the check."""
    target = resolve_inside(root, path, allow=allow)
    base = Path(root).resolve()
    return base, ("" if target == base else target.relative_to(base).as_posix())


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


def write_bytes_inside(root, rel: str, data: bytes, *, mode: int = 0o644, allow=(),
                       make_parents: bool = True, before_replace=None) -> Path:
    """Write ``data`` to the FIXED path ``root/<rel>``, creating a NEW file and renaming it into
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
                view = memoryview(data)
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


def write_text_inside(root, rel: str, text: str, *, mode: int = 0o644, allow=(),
                      make_parents: bool = True, before_replace=None) -> Path:
    """:func:`write_bytes_inside` for UTF-8 text."""
    return write_bytes_inside(root, rel, text.encode("utf-8"), mode=mode, allow=allow,
                              make_parents=make_parents, before_replace=before_replace)


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



_APPEND = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def append_text_inside(root, rel: str, text: str, *, mode: int = 0o600, allow=()) -> None:
    """Append ``text`` to ``root/<rel>`` (created if missing), the directories reached without
    following a link and the file opened ``O_APPEND|O_NOFOLLOW`` — and only appended to when it is a
    regular file with a single hard link. A link at the file or on the way raises
    :class:`PathRefused`; nothing is ever written through one."""
    parts = relative_parts(rel, allow=allow)
    *dirs, name = parts
    dir_fd = dir_fd_inside(root, dirs, create=True)
    try:
        try:
            fd = os.open(name, _APPEND, mode, dir_fd=dir_fd)
        except OSError as exc:
            raise PathRefused(REFUSAL, kind="symlink") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
                raise PathRefused(REFUSAL, kind="symlink")
            view = memoryview(text.encode("utf-8"))
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def _lstat_inside(root, rel: str, allow=()) -> "Optional[os.stat_result]":
    """``lstat`` of ``root/<rel>`` reached without following a link on the way; ``None`` when it is
    missing or a directory component is a link."""
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
        return os.lstat(name, dir_fd=dir_fd)
    except OSError:
        return None
    finally:
        os.close(dir_fd)


def stat_inside(root, rel: str, *, allow=()) -> "Optional[os.stat_result]":
    """``lstat`` of ``root/<rel>`` (the entry itself, a link not followed) reached without following
    a link on the way; ``None`` when it is missing or a directory component is a link."""
    return _lstat_inside(root, rel, allow)


def is_file_inside(root, rel: str, *, allow=()) -> bool:
    """True when ``root/<rel>`` is a regular file (not a link) reached without following a link."""
    st = _lstat_inside(root, rel, allow)
    return st is not None and stat.S_ISREG(st.st_mode)


def is_dir_inside(root, rel: str, *, allow=()) -> bool:
    """True when every component of ``root/<rel>`` is a real directory (none of them a link)."""
    try:
        fd = dir_fd_inside(root, relative_parts(rel, allow=allow))
    except (PathRefused, OSError):
        return False
    os.close(fd)
    return True


def _entries_inside(root, rel_dir: str, allow, want_dirs: bool, suffix: Optional[str]) -> list:
    parts = relative_parts(rel_dir, allow=allow) if rel_dir not in ("", ".") else []
    try:
        fd = dir_fd_inside(root, parts)
    except (PathRefused, OSError):
        return []
    try:
        with os.scandir(fd) as it:
            out = []
            for e in it:
                if suffix is not None and not e.name.endswith(suffix):
                    continue
                if (e.is_dir(follow_symlinks=False) if want_dirs
                        else e.is_file(follow_symlinks=False)):
                    out.append(e.name)
            return sorted(out)
    except OSError:
        return []
    finally:
        os.close(fd)


def list_files_inside(root, rel_dir: str = "", *, suffix: Optional[str] = None, allow=()) -> list:
    """Sorted names of the REGULAR files (never a link) directly in ``root/<rel_dir>``, the directory
    reached without following a link; ``[]`` when it is missing or reached through one."""
    return _entries_inside(root, rel_dir, allow, False, suffix)


def list_dirs_inside(root, rel_dir: str = "", *, allow=()) -> list:
    """Sorted names of the real subdirectories (never a link) directly in ``root/<rel_dir>``."""
    return _entries_inside(root, rel_dir, allow, True, None)


def read_head_inside(root, rel: str, nbytes: int, *, allow=()) -> Optional[str]:
    """At most ``nbytes`` of ``root/<rel>`` decoded as UTF-8 (errors replaced), read without
    following a link — for a front-matter peek at a page that may be long. ``None`` on the same
    refusals as :func:`read_text_inside` (size aside)."""
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
        return os.read(fd, nbytes).decode("utf-8", errors="replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def walk_files_inside(root, rel_dir: str = "", *, allow=()):
    """Every regular file (never a link) at any depth under ``root/<rel_dir>``, as a path relative
    to ``root`` (POSIX). The walk is descriptor-based and follows no link — a symlinked directory is
    neither entered nor reported, a symlinked file is skipped — so nothing outside the tree, and no
    second name for something inside it, is ever reached."""
    parts = relative_parts(rel_dir, allow=allow) if rel_dir not in ("", ".") else []
    try:
        top = dir_fd_inside(root, parts)
    except (PathRefused, OSError):
        return
    prefix = "/".join(parts)
    try:
        for dirpath, _dirnames, filenames, dfd in os.fwalk(".", dir_fd=top, follow_symlinks=False):
            for name in sorted(filenames):
                try:
                    st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                inner = os.path.normpath(os.path.join(dirpath, name)).replace(os.sep, "/")
                yield f"{prefix}/{inner}" if prefix else inner
    finally:
        os.close(top)


def copy_file_inside(src, root, rel: str, *, allow=()) -> None:
    """Copy the TRUSTED file ``src`` (one an image ships: a seed, a preset) to ``root/<rel>`` through
    :func:`write_bytes_inside`, keeping its permission bits — a link at the destination is replaced,
    one on the way refuses (``PathRefused``). ``OSError`` when ``src`` cannot be read."""
    sp = Path(src)
    data = sp.read_bytes()
    write_bytes_inside(root, rel, data, mode=stat.S_IMODE(sp.stat().st_mode), allow=allow)


def copy_tree_inside(src, root, rel_dir: str = "", *, allow=()) -> list:
    """Copy the TRUSTED tree ``src`` (an image directory: a seed, a preset library) into
    ``root/<rel_dir>``, every write :func:`write_bytes_inside` — so a link planted anywhere in the
    destination is replaced at a file or refuses at a directory, never written through. Files keep
    their permission bits. A destination path that is refused is skipped and returned."""
    refused: list = []
    srcp = Path(src)
    for dirpath, _dirnames, filenames in os.walk(srcp):
        for name in sorted(filenames):
            sp = Path(dirpath) / name
            inner = sp.relative_to(srcp).as_posix()
            dest = f"{rel_dir}/{inner}" if rel_dir else inner
            try:
                copy_file_inside(sp, root, dest, allow=allow)
            except PathRefused:
                refused.append(dest)
            except OSError:
                continue
    return refused


def clear_tree_inside(root, *, keep=()) -> None:
    """Remove every entry directly in ``root`` except the names in ``keep``, never following a link:
    a symlink or regular file is unlinked (the link itself), a real directory is removed with
    ``shutil.rmtree`` relative to ``root``'s descriptor (which does not follow links inside it and
    refuses an entry that turned into a link). Errors on one entry do not stop the rest."""
    import shutil
    fd = _open_root(root)
    try:
        with os.scandir(fd) as it:
            names = [e.name for e in it if e.name not in set(keep)]
        for name in names:
            try:
                st = os.lstat(name, dir_fd=fd)
            except OSError:
                continue
            try:
                if stat.S_ISDIR(st.st_mode):
                    shutil.rmtree(name, dir_fd=fd)
                else:
                    os.unlink(name, dir_fd=fd)
            except OSError:
                continue
    finally:
        os.close(fd)


def remove_tree(path, *, ignore_errors: bool = False) -> None:
    """Remove whatever is at ``path`` without following a link: a link (or a plain file) is unlinked
    itself, and a directory is removed by ``shutil.rmtree`` relative to its parent's descriptor —
    the descriptor-based walk, which unlinks a link inside the tree instead of descending into it
    and refuses an entry swapped for a link mid-walk. Absent is a no-op.

    The parent is opened as given, like a mount root: callers name a whole tree by a path the
    platform owns (a slot under ``.attached/``, a staging or temp directory), never one a tool can
    redirect. Outside this module, this is the only ``rmtree`` the work-tree gate admits."""
    import shutil
    p = Path(path)
    try:
        fd = _open_root(p.parent)
    except FileNotFoundError:
        return
    except OSError:
        if ignore_errors:
            return
        raise
    try:
        try:
            st = os.lstat(p.name, dir_fd=fd)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(st.st_mode):
            shutil.rmtree(p.name, dir_fd=fd, ignore_errors=ignore_errors)
        else:
            os.unlink(p.name, dir_fd=fd)
    except OSError:
        if not ignore_errors:
            raise
    finally:
        os.close(fd)


def ensure_git_exclude(root, line: str) -> None:
    """Make sure ``line`` is in ``root/.git/info/exclude`` (git's per-clone ignore list) — when
    ``root/.git`` is a real directory, never through a link at ``.git``, ``info`` or the file. A
    workspace with no repository (or one whose ``.git`` is a link) is left alone."""
    if not is_dir_inside(root, ".git", allow=(".git",)):
        return
    rel = ".git/info/exclude"
    body = read_text_inside(root, rel, allow=(".git",)) or ""
    if line in body.splitlines():
        return
    write_text_inside(root, rel, body.rstrip("\n") + ("\n" if body.strip() else "") + line + "\n",
                      allow=(".git",))
