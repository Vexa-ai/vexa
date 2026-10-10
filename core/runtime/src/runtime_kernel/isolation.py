"""isolation.py — the POSIX identity of every child the PROCESS backend (the lite deployment) starts.

Docker/k8s workers get tenant isolation from the mount table (one bind per mount — see mounts.py):
another tenant's workspace simply isn't in the container. Lite workers are CHILD PROCESSES sharing one
filesystem, so the wall must be the kernel's instead.

Under a root runtime, NO child runs as root:

* a workload that names workspaces (an agent worker) runs as its SUBJECT's uid — private tiers are
  ``0700``-owned by that uid, and each shared workspace gets its OWN gid (allocated once, persisted in
  a root-owned registry at the store root) that member workers join as a supplementary group.
  ``_global`` stays root-owned world-readable — enforced read-only for everyone;
* a workload with no workspace (a meeting bot) runs as a uid of its OWN, unique among the live
  workloads, with only the supplementary groups its profile names;
* every child gets a FRESH private HOME (and TMPDIR inside it), made by root in a root-owned directory
  and handed over only once it is complete, so root never writes into a path a child could have
  prepared.

A subject maps to a uid deterministically: a canonical ASCII decimal below ``NUMERIC_SUBJECT_LIMIT``
is ``UID_BASE + n``; anything else (another spelling, a name) is allocated once from
``SUBJECT_UID_BASE`` and persisted in a root-owned registry at the store root. A subject that is
empty, too long or not a plain name is refused. When anything here cannot be done the spawn is
REFUSED (:class:`IsolationRefused`) — a root runtime never falls back to a root child.

Root does its filesystem work on directory file descriptors with ``O_NOFOLLOW``: the store root's
path is checked from ``/`` (every component root-owned, none writable by others unless sticky), the
tiers are opened without following links, a tenant's tree is re-owned by an fd walk that never
follows a link and leaves hard-linked files alone, and the registries are read and replaced without
following links.

A runtime that is not root cannot change uids: its children run as its own (non-root) uid, and a
workspace dispatch says loudly that the per-subject wall is unavailable.
"""
from __future__ import annotations

import errno
import fcntl
import grp
import json
import logging
import os
import re
import secrets
import shutil
import stat
import threading
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Mapping, Optional

from . import userns
from .mounts import mount_set

logger = logging.getLogger("runtime_kernel.isolation")

UID_BASE = 100000              # canonical numeric subject n → uid UID_BASE + n (and gid = uid)
GID_BASE = 200000              # per-shared-workspace gids, allocated sequentially from here
#: Numeric subjects at or past this map through the registry, so an arithmetic uid (which is also the
#: subject's primary gid) never reaches the shared-workspace gids.
NUMERIC_SUBJECT_LIMIT = GID_BASE - UID_BASE
SUBJECT_UID_BASE = 1_000_000_000   # every other subject: allocated once from here, persisted
SUBJECT_UID_LIMIT = 1_400_000_000
GID_LIMIT = SUBJECT_UID_BASE       # the shared-workspace gids never reach the subject range
WORKLOAD_UID_BASE = 1_500_000_000  # a workload with no subject: one uid per live workload
WORKLOAD_UID_SLOTS = 1_000_000
GID_REGISTRY = ".vexa-shared-gids.json"   # root-owned 0600, at the store root
UID_REGISTRY = ".vexa-subject-uids.json"  # root-owned 0600, at the store root
#: Where the children's private HOMEs are made (root-owned 0711; each HOME 0700, one per child).
HOMES_ROOT = "/var/lib/vexa-runtime/homes"
MAX_SUBJECT_LEN = 256

_NUMERIC = re.compile(r"0|[1-9][0-9]{0,17}")                  # ASCII only: str.isdigit() is not
_SUBJECT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._@+=:-]*")      # one plain path component

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_PATH = getattr(os, "O_PATH", 0)
_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | os.O_NOFOLLOW | _O_CLOEXEC
#: Opening a tree entry only to fstat and re-own it: O_PATH where the platform has it (no read, no
#: device side effects), else a non-blocking read open.
_LEAF_FLAGS = ((_O_PATH or (os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0)))
               | os.O_NOFOLLOW | _O_CLOEXEC)

_registry_lock = threading.Lock()


class IsolationRefused(RuntimeError):
    """Isolation cannot be applied to this child, so the child must not start."""


@dataclass(frozen=True)
class ProcessIsolation:
    """One workspace dispatch's plan: run as ``uid`` (gid = uid, + shared-workspace ``groups``);
    ``private`` dirs are owned ``uid`` mode 0700; ``shared`` dirs owned root:<gid> mode 2770.
    ``uid`` is ``None`` until :func:`apply_process_isolation` resolves a registry-mapped subject."""

    subject: str
    store_root: str
    uid: Optional[int] = None
    private: tuple[str, ...] = ()
    shared: tuple[tuple[str, str], ...] = ()   # (path, workspace slug/id) — gid resolved at apply
    groups: tuple[int, ...] = field(default=(), compare=False)  # filled by apply (registry-backed)

    @property
    def gid(self) -> Optional[int]:
        return self.uid


@dataclass(frozen=True)
class ChildIdentity:
    """Who one child runs as, and the private HOME made for it."""

    uid: int
    gid: int
    groups: tuple[int, ...]
    home: str
    tmp: str


# ── subjects ──────────────────────────────────────────────────────────────────────────────────────

def numeric_uid(subject: str) -> Optional[int]:
    """The arithmetic uid of a canonical ASCII decimal subject below ``NUMERIC_SUBJECT_LIMIT``."""
    if _NUMERIC.fullmatch(subject) and int(subject) < NUMERIC_SUBJECT_LIMIT:
        return UID_BASE + int(subject)
    return None


def check_subject(subject: str) -> str:
    """The subject, or :class:`IsolationRefused` when it is not one plain name a uid can be kept for."""
    if not subject:
        raise IsolationRefused("the dispatch names no subject (VEXA_OWNER)")
    if len(subject) > MAX_SUBJECT_LEN or not _SUBJECT.fullmatch(subject):
        raise IsolationRefused("the subject is not a plain name (letters, digits and . _ @ + = : -)")
    return subject


def plan_process_isolation(env: Mapping[str, str], *, euid: Optional[int] = None) -> Optional[ProcessIsolation]:
    """Env → the plan for a workspace dispatch, or ``None`` for a workload that names no workspace.

    Under a root runtime a workspace dispatch whose plan cannot be made raises
    :class:`IsolationRefused`. A non-root runtime returns ``None`` with ONE loud warning."""
    if euid is None:
        euid = os.geteuid()
    mounts = mount_set(env)
    # The store root is the runtime's own (workload_env injects it); a dispatch cannot move it.
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET") or ""
    if not root and not mounts:
        return None                                   # a bot: no workspace, planned elsewhere
    if euid != 0:
        logger.warning("workspace isolation UNAVAILABLE for this dispatch (runtime is not root — "
                       "cannot setuid workers); the worker runs as the runtime's own uid. Run lite's "
                       "runtime as root.")
        return None
    if not root:
        raise IsolationRefused("the dispatch names workspaces but the runtime has no store root")
    subject = check_subject((env.get("VEXA_OWNER") or "").strip())
    private: list[str] = []
    shared: list[tuple[str, str]] = []
    for m in mounts:
        path, role = m.get("path") or "", m.get("role") or "private"
        if not path:
            continue
        if role in ("private", "system"):
            private.append(path)
        elif role == "shared":
            shared.append((path, str(m.get("slug") or os.path.basename(path.rstrip("/")))))
        # role == "global": root-owned world-readable — enforced ro by ownership, nothing to do
    return ProcessIsolation(subject=subject, store_root=root, uid=numeric_uid(subject),
                            private=tuple(private), shared=tuple(shared))


# ── safe filesystem primitives (root's view) ──────────────────────────────────────────────────────

def _close(fd: Optional[int]) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _trusted_owner(st: os.stat_result, owner: int) -> bool:
    return st.st_uid in (0, owner)


def open_trusted_dir(path: str, *, owner: int = 0, create_mode: Optional[int] = None) -> int:
    """Open ``path`` as a directory only ``owner`` (or root) controls, walking the path AS GIVEN from
    ``/`` with ``O_NOFOLLOW``: every component must be a directory (a link anywhere refuses) owned by
    root or ``owner``, and none above the last may be writable by group or others unless sticky. A
    missing component is created (owned by whoever runs this) when ``create_mode`` is given. Returns
    an fd; :class:`IsolationRefused` on any component that fails."""
    if not path.startswith("/"):
        raise IsolationRefused(f"{path!r} is not an absolute path")
    real = os.path.normpath(path)
    parts = [p for p in real.split("/") if p]
    if ".." in parts:
        raise IsolationRefused(f"{path!r} names a parent directory")
    fd = os.open("/", _DIR_FLAGS)
    try:
        for i, name in enumerate(parts):
            last = i == len(parts) - 1
            try:
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if create_mode is None:
                    raise
                try:
                    os.mkdir(name, create_mode if last else 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError as e:
                raise IsolationRefused(f"{real}: component {name!r} is not a plain directory ({e.strerror})") from e
            _close(fd)
            fd = nfd
            st = os.fstat(fd)
            if not _trusted_owner(st, owner):
                raise IsolationRefused(f"{real}: component {name!r} is owned by uid {st.st_uid}")
            if not last and st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX:
                raise IsolationRefused(f"{real}: component {name!r} is writable by others")
        out, fd = fd, None
        return out
    finally:
        _close(fd)


def _open_dir_at(parent_fd: int, name: str) -> Optional[int]:
    """``name`` under ``parent_fd`` as a directory, never through a link. ``None`` when absent;
    :class:`IsolationRefused` when it is a link or not a directory."""
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise IsolationRefused(f"{name!r} is a link or not a directory") from e
        raise


def _open_rel_dir(root_fd: int, rel: str) -> Optional[int]:
    """Walk a store-relative path from the store root fd, every component without following a link.
    ``None`` when a component is absent."""
    parts = [p for p in rel.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise IsolationRefused(f"{rel!r} leaves the store")
    fd = os.dup(root_fd)
    try:
        for name in parts:
            nfd = _open_dir_at(fd, name)
            _close(fd)
            fd = nfd
            if fd is None:
                return None
        out, fd = fd, None
        return out
    finally:
        _close(fd)


def _reown_leaf(name: str, dir_fd: int, uid: int, gid: int) -> None:
    """Re-own one non-directory entry: a regular file with exactly one link, never a link itself."""
    try:
        fd = os.open(name, _LEAF_FLAGS, dir_fd=dir_fd)
    except OSError:
        return                                        # vanished, a link (ELOOP), a FIFO with no peer
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (st.st_uid, st.st_gid) == (uid, gid):
            return
        if st.st_nlink != 1:
            logger.warning("not re-owning a hard-linked file in a tenant tree (%s)", name)
            return
        if _O_PATH:
            os.chown(f"/proc/self/fd/{fd}", uid, gid)   # the exact inode the fd names
        else:
            os.fchown(fd, uid, gid)
    except OSError as e:
        logger.warning("chown failed for %s: %s", name, e.strerror)
    finally:
        _close(fd)


def chown_tree(top_fd: int, uid: int, gid: int) -> None:
    """Re-own the tree under ``top_fd`` without ever following a link: directories are re-owned
    through the fds an ``fwalk`` opens, files through an ``O_NOFOLLOW`` open of each entry. Links,
    special files and hard-linked files keep their owner."""
    os.fchown(top_fd, uid, gid)
    for _dirpath, _dirs, files, dfd in os.fwalk(".", dir_fd=top_fd, follow_symlinks=False):
        try:
            os.fchown(dfd, uid, gid)
        except OSError as e:
            logger.warning("chown failed for a directory: %s", e.strerror)
        for name in files:
            _reown_leaf(name, dfd, uid, gid)


def _read_registry(dir_fd: int, name: str, owner: int, base: int, limit: int) -> dict[str, int]:
    """A registry at the store root: a regular file root (``owner``) wrote — exactly one link, no
    access for group or others — holding plain-name keys and ids inside ``[base, limit)``. Anything
    else refuses: the registry decides uids and gids, so a file that does not look like root's own
    is not trusted."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | _O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise IsolationRefused(f"registry {name} cannot be read safely ({e.strerror})") from e
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        st = os.fstat(f.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != owner or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) & 0o077):
            raise IsolationRefused(f"registry {name} is not a private file of the store's owner")
        try:
            data = json.load(f)
        except ValueError as e:
            raise IsolationRefused(f"registry {name} is not JSON") from e
    if not isinstance(data, dict):
        raise IsolationRefused(f"registry {name} is not a JSON object")
    out: dict[str, int] = {}
    for key, value in data.items():
        if (not isinstance(key, str) or not _SUBJECT.fullmatch(key) or len(key) > MAX_SUBJECT_LEN
                or type(value) is not int or not base <= value < limit):
            raise IsolationRefused(f"registry {name} holds an entry outside its range")
        out[key] = value
    if len(set(out.values())) != len(out):
        raise IsolationRefused(f"registry {name} gives one id to two names")
    return out


def _replace_json_at(dir_fd: int, name: str, data: dict) -> None:
    tmp = f"{name}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _O_CLOEXEC, 0o600,
                 dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fchmod(f.fileno(), 0o600)
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def _allocate(root_fd: int, registry: str, key: str, base: int, limit: int, *, owner: int = 0) -> int:
    """The id ``key`` holds in ``registry`` — allocated once (next after the highest), persisted."""
    with _registry_lock:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        try:
            reg = _read_registry(root_fd, registry, owner, base, limit)
            if key in reg:
                return reg[key]
            value = max(reg.values(), default=base - 1) + 1
            if value >= limit:
                raise IsolationRefused(f"{registry} is full")
            reg[key] = value
            _replace_json_at(root_fd, registry, reg)
            return value
        finally:
            fcntl.flock(root_fd, fcntl.LOCK_UN)


# ── apply (store-level effects) ───────────────────────────────────────────────────────────────────

_SPECIAL = {".attached", ".system", ".home", "_global", GID_REGISTRY, UID_REGISTRY}


def _sweep_default_deny(tier_fd: int) -> None:
    """DEFAULT-DENY for tenants that never dispatched since isolation shipped: every tenant dir in
    the tier whose mode is still open gets 0700 (owner unchanged — the owner's own next dispatch
    re-owns it). Top level only (0700 on the top seals the tree), opened without following links.
    Dirs already sealed (0700) or group-managed (2770 shared) are left alone."""
    for entry in os.scandir(tier_fd):
        if entry.name in _SPECIAL:
            continue
        try:
            fd = _open_dir_at(tier_fd, entry.name)
        except IsolationRefused:
            continue                                  # a link or a file: not a tenant dir
        if fd is None:
            continue
        try:
            mode = stat.S_IMODE(os.fstat(fd).st_mode)
            if mode not in (0o700, 0o2770):
                os.fchmod(fd, 0o700)
        finally:
            _close(fd)


def _tier(root_fd: int, name: str, owner: int) -> Optional[int]:
    """A store tier (``.attached``, ``.system``): root-owned, traversable, not listable (0711)."""
    fd = _open_dir_at(root_fd, name)
    if fd is None:
        return None
    st = os.fstat(fd)
    if not _trusted_owner(st, owner) or st.st_mode & 0o022:
        os.close(fd)
        raise IsolationRefused(f"store tier {name} is not the store owner's alone")
    if stat.S_IMODE(st.st_mode) != 0o711:
        os.fchmod(fd, 0o711)                          # tighten: traversable, not listable
    return fd


def apply_process_isolation(plan: ProcessIsolation, *, owner: int = 0) -> ProcessIsolation:
    """Materialize the plan on the store: traversal perms on the root and tiers, the DEFAULT-DENY
    sweep over every tenant dir, the subject's uid (registry-allocated when not numeric), private
    0700 trees, shared 2770 group trees. Idempotent and cheap when ownership already matches.
    ``owner`` is who the store's own directories belong to (root in production). Returns the plan
    with ``uid`` and the shared-workspace ``groups`` resolved; raises on anything it cannot do."""
    root = plan.store_root
    root_fd = open_trusted_dir(root, owner=owner)
    fds: list[Optional[int]] = []
    try:
        st = os.fstat(root_fd)
        if st.st_uid != owner:
            raise IsolationRefused(f"store root {root} is owned by uid {st.st_uid}")
        if st.st_mode & 0o022:
            # anything could have been planted while others could write here: refuse, and let the
            # deployment (Lite's entrypoint) set the store root right before the runtime starts
            raise IsolationRefused(f"store root {root} is writable by group or others")
        uid = plan.uid
        if uid is None:
            uid = _allocate(root_fd, UID_REGISTRY, plan.subject, SUBJECT_UID_BASE, SUBJECT_UID_LIMIT,
                            owner=owner)
        tiers = {name: _tier(root_fd, name, owner) for name in (".attached", ".system")}
        fds.extend(tiers.values())
        # seal EVERY tenant dir, not just this dispatch's — a never-dispatched tenant's data must not
        # sit world-readable while it waits for its owner's first isolated dispatch
        for tier_fd in (root_fd, *(fd for fd in tiers.values() if fd is not None)):
            _sweep_default_deny(tier_fd)
        # the .attached/<subject> parent dir is the subject's too (their slots live under it)
        private = list(plan.private)
        if tiers[".attached"] is not None:
            private.append(os.path.join(root, ".attached", plan.subject))
        for path in private:
            fd = _open_rel_dir(root_fd, os.path.relpath(path, root))
            if fd is None:
                continue
            try:
                st = os.fstat(fd)
                if st.st_uid != uid or stat.S_IMODE(st.st_mode) != 0o700:
                    chown_tree(fd, uid, uid)
                    os.fchmod(fd, 0o700)
            finally:
                _close(fd)
        groups: list[int] = []
        for path, ws_id in plan.shared:
            fd = _open_rel_dir(root_fd, os.path.relpath(path, root))
            if fd is None:
                continue
            try:
                gid = _allocate(root_fd, GID_REGISTRY, ws_id, GID_BASE, GID_LIMIT, owner=owner)
                groups.append(gid)
                st = os.fstat(fd)
                if st.st_gid != gid or stat.S_IMODE(st.st_mode) != 0o2770:
                    chown_tree(fd, owner, gid)
                    os.fchmod(fd, 0o2770)             # setgid: new files inherit the workspace group
            finally:
                _close(fd)
        return replace(plan, uid=uid, groups=tuple(groups))
    finally:
        for fd in fds:
            _close(fd)
        _close(root_fd)


# ── private HOMEs ─────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StagedFile:
    """A file copied into a child's fresh HOME: ``source`` (read by the runtime) lands at
    ``home_path`` (relative to the HOME), owned by the child, mode 0400."""

    source: str
    home_path: str


def _home_parts(home_path: str) -> list[str]:
    parts = [p for p in home_path.split("/") if p]
    if not parts or home_path.startswith("/") or any(p in (".", "..") for p in parts):
        raise IsolationRefused(f"staged file path {home_path!r} is not relative to the HOME")
    return parts


def make_home(uid: int, gid: int, *, homes_root: str = HOMES_ROOT, owner: int = 0,
              staged: Iterable[StagedFile] = ()) -> tuple[str, str]:
    """A FRESH private HOME for one child, and the TMPDIR inside it: made by root (``owner``) as a
    new 0700 directory in a root-owned 0711 parent, filled (``tmp/``, the staged files) while only
    root can reach it, then handed to ``uid``/``gid`` innermost first. Nothing a child wrote before
    is reused. Returns ``(home, tmp)``."""
    parent = open_trusted_dir(homes_root, owner=owner, create_mode=0o711)
    created: list[int] = []
    home_fd = None
    try:
        if stat.S_IMODE(os.fstat(parent).st_mode) != 0o711:
            os.fchmod(parent, 0o711)
        for _ in range(8):
            name = f"{uid}.{secrets.token_hex(8)}"
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
                break
            except FileExistsError:
                continue
        else:
            raise IsolationRefused("could not make a fresh HOME")
        home_fd = os.open(name, _DIR_FLAGS, dir_fd=parent)
        os.mkdir("tmp", 0o700, dir_fd=home_fd)
        created.append(os.open("tmp", _DIR_FLAGS, dir_fd=home_fd))
        for item in staged:
            parts = _home_parts(item.home_path)
            try:
                with open(item.source, "rb") as f:
                    data = f.read()
            except OSError as e:
                logger.warning("credential file %s not staged (%s)", item.source, e.strerror)
                continue
            dfd = os.dup(home_fd)
            for d in parts[:-1]:
                try:
                    os.mkdir(d, 0o700, dir_fd=dfd)
                except FileExistsError:
                    pass
                nfd = os.open(d, _DIR_FLAGS, dir_fd=dfd)
                _close(dfd)
                dfd = nfd
                created.append(os.dup(dfd))
            try:
                ffd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _O_CLOEXEC,
                              0o400, dir_fd=dfd)
            finally:
                _close(dfd)
            with os.fdopen(ffd, "wb") as out:
                out.write(data)
                os.fchmod(out.fileno(), 0o400)
                os.fchown(out.fileno(), uid, gid)
        for fd in reversed(created):                  # innermost first, the HOME itself last
            os.fchown(fd, uid, gid)
        os.fchown(home_fd, uid, gid)
        home = os.path.join(os.path.normpath(homes_root), name)
        return home, os.path.join(home, "tmp")
    finally:
        for fd in created:
            _close(fd)
        _close(home_fd)
        _close(parent)


def sweep_homes(*, homes_root: str = HOMES_ROOT, owner: int = 0,
                live_uids: Optional[Callable[[], set[int]]] = None) -> int:
    """Remove the HOMEs whose uid no live process holds (left by a runtime that stopped before its
    children's teardown). Returns how many were removed."""
    real = os.path.normpath(homes_root)
    try:
        names = os.listdir(real)
    except OSError:
        return 0
    live = (live_uids or _live_uids)()
    removed = 0
    for name in names:
        uid, _, rest = name.partition(".")
        if not uid.isascii() or not uid.isdigit() or not rest or int(uid) in live:
            continue
        remove_home(os.path.join(real, name), homes_root=homes_root, owner=owner)
        removed += 1
    return removed


def remove_home(home: str, *, homes_root: str = HOMES_ROOT, owner: int = 0) -> None:
    """Remove a HOME :func:`make_home` made (fd-based, never through a link). Best-effort."""
    if os.path.dirname(home) != os.path.normpath(homes_root):
        logger.warning("not removing %s: not a HOME this runtime made", home)
        return
    try:
        parent = open_trusted_dir(homes_root, owner=owner)
    except (OSError, IsolationRefused):
        return
    try:
        shutil.rmtree(os.path.basename(home), dir_fd=parent)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("could not remove HOME %s: %s", home, e.strerror)
    finally:
        _close(parent)


# ── per-workload uids (workloads that name no subject) ────────────────────────────────────────────

def _live_uids(proc: str = "/proc") -> set[int]:
    """Every real/effective/saved uid a live process holds (empty where there is no /proc)."""
    out: set[int] = set()
    try:
        entries = os.listdir(proc)
    except OSError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc, entry, "status"), "rb") as f:
                for line in f:
                    if line.startswith(b"Uid:"):
                        out.update(int(v) for v in line.split()[1:4])
                        break
        except (OSError, ValueError):
            continue
    return out


class WorkloadUids:
    """Hands each live workload with no subject a uid of its own from ``WORKLOAD_UID_BASE``: never one
    another live workload holds, never one any live process still runs as (a child the runtime lost
    track of across a restart keeps its uid to itself)."""

    def __init__(self, base: int = WORKLOAD_UID_BASE, slots: int = WORKLOAD_UID_SLOTS,
                 live_uids: Callable[[], set[int]] = _live_uids) -> None:
        self._base, self._slots, self._live_uids = base, slots, live_uids
        self._held: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, workload_id: str) -> int:
        with self._lock:
            if workload_id in self._held:
                return self._held[workload_id]
            busy = set(self._held.values()) | self._live_uids()
            for uid in range(self._base, self._base + self._slots):
                if uid not in busy:
                    self._held[workload_id] = uid
                    return uid
        raise IsolationRefused("no free workload uid")

    def release(self, workload_id: str) -> None:
        with self._lock:
            self._held.pop(workload_id, None)


def group_ids(names: Iterable[str]) -> tuple[int, ...]:
    """The gids of the host groups a profile names; a name the host lacks is skipped (logged)."""
    out: list[int] = []
    for name in names:
        try:
            out.append(grp.getgrnam(name).gr_gid)
        except KeyError:
            logger.info("host group %r does not exist here; not joined", name)
    return tuple(g for g in out if g != 0)


# ── the drop ──────────────────────────────────────────────────────────────────────────────────────

def _no_new_privs() -> Callable[[], None]:
    """``prctl(PR_SET_NO_NEW_PRIVS)`` for the forked child, resolved before the fork: no set-uid
    program can raise the child's privileges again. A host where it cannot be resolved refuses the
    spawn (:class:`IsolationRefused`) rather than starting a child without it."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
    except (OSError, AttributeError) as e:
        raise IsolationRefused("prctl(PR_SET_NO_NEW_PRIVS) is not available here") from e

    def _set() -> None:
        if prctl(38, 1, 0, 0, 0) != 0:               # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
    return _set


def preexec_for(identity: ChildIdentity, *, user_namespaces: bool = False) -> Callable[[], None]:
    """The Popen ``preexec_fn`` dropping the forked child to ``identity`` (groups → gid → uid, in that
    order — after setuid the process can no longer change groups), then proving the drop: a child
    that still holds uid 0 or gid 0 anywhere raises, and Popen fails the spawn. Unless its profile
    says it may (``user_namespaces``: a meeting bot, for Chromium's sandbox), the child then loses
    the ability to create a user namespace, for itself and all it starts (runtime_kernel.userns); a
    machine without that filter refuses the spawn."""
    if identity.uid == 0 or identity.gid == 0:
        raise IsolationRefused("refusing to run a child as root")
    no_new_privs = _no_new_privs()
    refuse_userns: Optional[Callable[[], None]] = None
    if not user_namespaces:
        try:
            refuse_userns = userns.refusal()
        except OSError as e:
            raise IsolationRefused(f"cannot refuse the child user namespaces here ({e})") from e

    def _drop() -> None:
        os.setgroups(list(identity.groups))
        os.setgid(identity.gid)
        os.setuid(identity.uid)
        uids = os.getresuid() if hasattr(os, "getresuid") else (os.getuid(), os.geteuid())
        gids = os.getresgid() if hasattr(os, "getresgid") else (os.getgid(), os.getegid())
        if 0 in uids or 0 in gids or 0 in os.getgroups():
            raise OSError(errno.EPERM, "the child still holds root")
        no_new_privs()
        if refuse_userns is not None:
            refuse_userns()
    return _drop


def child_env_for(identity: ChildIdentity, env: Mapping[str, str]) -> dict[str, str]:
    """The child's env with its private HOME and the TMPDIR inside it (scratch files never collide
    across children in /tmp)."""
    return {**env, "HOME": identity.home, "TMPDIR": identity.tmp}
