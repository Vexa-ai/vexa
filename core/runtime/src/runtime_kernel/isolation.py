"""isolation.py — POSIX tenant isolation for the PROCESS backend (the lite deployment).

Docker/k8s workers get tenant isolation from the mount table (one bind per mount — see mounts.py):
another tenant's workspace simply isn't in the container. Lite workers are CHILD PROCESSES sharing one
filesystem, so the wall must be the kernel's instead: every dispatch runs as a PER-SUBJECT uid, private
tiers are ``0700``-owned by that uid, and each shared workspace gets its OWN gid (allocated once,
persisted in a root-owned registry at the store root) that member workers join as a supplementary
group. ``_global`` stays root-owned world-readable — enforced read-only for everyone.

Split for testability:
  * :func:`plan_process_isolation` — PURE: env → the uid/gid/dir plan (or None + reason when
    unavailable). Unit-tested offline.
  * :func:`apply_process_isolation` — effects: allocate gids, chown/chmod the plan's dirs. Idempotent
    and cheap when ownership already matches (a full ``chown -R`` runs only on a mismatched tree).
  * :func:`preexec_for` — the ``subprocess.Popen(preexec_fn=…)`` that drops the child to the plan's
    uid/gid/groups. Runs in the forked child, pre-exec.

Requires euid 0 (lite's runtime runs as root inside its container) and a NUMERIC subject (gateway
user ids). Anything else degrades LOUDLY to the shared-trust behavior — never a silent half-wall.
"""
from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

from .mounts import isolation_mode, mount_set

logger = logging.getLogger("runtime_kernel.isolation")

UID_BASE = 100000   # per-subject uid = UID_BASE + int(subject)
GID_BASE = 200000   # per-shared-workspace gids, allocated sequentially from here
GID_REGISTRY = ".vexa-shared-gids.json"   # root-owned, at the store root


@dataclass(frozen=True)
class ProcessIsolation:
    """One dispatch's POSIX plan: run as ``uid``/``gid`` (+ shared-workspace ``groups``); ``private``
    dirs are owned ``uid`` mode 0700; ``shared`` dirs owned root:<gid> mode 2770; ``home`` is a
    per-subject writable HOME (harness config/creds land there, not in /root)."""

    uid: int
    gid: int
    store_root: str
    home: str
    private: tuple[str, ...] = ()
    shared: tuple[tuple[str, str], ...] = ()   # (path, workspace slug/id) — gid resolved at apply
    groups: tuple[int, ...] = field(default=(), compare=False)  # filled by apply (registry-backed)


def plan_process_isolation(env: Mapping[str, str], *, euid: Optional[int] = None) -> Optional[ProcessIsolation]:
    """Env → the isolation plan, or ``None`` (with ONE loud log naming why) when unavailable."""
    if isolation_mode(env) != "strict":
        return None
    if euid is None:
        euid = os.geteuid()
    subject = (env.get("VEXA_OWNER") or "").strip()
    mounts = mount_set(env)
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET") or env.get("VEXA_WORKSPACES_DIR") or ""
    if not root:
        # the process backend serves bots too (no workspace env) — nothing to isolate, not an error
        return None if not mounts else _unavailable("no workspace store root in the dispatch env")
    if euid != 0:
        return _unavailable("runtime is not root — cannot setuid workers (run lite's runtime as root)")
    if not subject.isdigit():
        return _unavailable(f"subject {subject!r} is not numeric — no deterministic uid mapping")
    uid = UID_BASE + int(subject)
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
    home = os.path.join(root, ".home", subject)
    return ProcessIsolation(uid=uid, gid=uid, store_root=root, home=home,
                            private=tuple(private), shared=tuple(shared))


def _unavailable(reason: str) -> None:
    logger.warning("workspace isolation UNAVAILABLE for this dispatch (%s) — worker runs shared-trust "
                   "(legacy). Fix the condition or set VEXA_WORKSPACE_ISOLATION=legacy to silence.", reason)
    return None


# ── effects ────────────────────────────────────────────────────────────────────────────────────────

def _load_registry(root: str) -> dict[str, int]:
    try:
        with open(os.path.join(root, GID_REGISTRY), encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _shared_gid(root: str, ws_id: str) -> int:
    """The workspace's gid — allocated once, persisted root-owned (0600) at the store root."""
    reg = _load_registry(root)
    if ws_id in reg:
        return reg[ws_id]
    gid = max(reg.values(), default=GID_BASE - 1) + 1
    reg[ws_id] = gid
    path = os.path.join(root, GID_REGISTRY)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return gid


def _chown_tree(path: str, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)
    for base, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(base, name)
            try:
                os.lchown(p, uid, gid)
            except OSError:
                logger.warning("chown failed under %s: %s", path, p)


def _sweep_default_deny(root: str) -> None:
    """DEFAULT-DENY for tenants that never dispatched since isolation shipped: every tenant-owned dir
    in the store whose mode is still open gets 0700 (owner unchanged — the owner's own next dispatch
    chowns it properly). Top-level only per tier (no recursion — 0700 on the top seals the tree), so
    the sweep is O(#tenants) stat calls per dispatch. Skips dirs already sealed (0700) or already
    group-managed (a shared workspace some member's dispatch set to 2770)."""
    special = {".attached", ".system", ".home", "_global", GID_REGISTRY}
    tiers = [root, os.path.join(root, ".attached"), os.path.join(root, ".system")]
    for tier in tiers:
        try:
            entries = list(os.scandir(tier))
        except OSError:
            continue
        for e in entries:
            if e.name in special or not e.is_dir(follow_symlinks=False):
                continue
            mode = stat.S_IMODE(e.stat(follow_symlinks=False).st_mode)
            if mode in (0o700, 0o2770):
                continue
            os.chmod(e.path, 0o700)


def apply_process_isolation(plan: ProcessIsolation) -> ProcessIsolation:
    """Materialize the plan: store-root traversal perms, DEFAULT-DENY sweep over every tenant dir,
    per-subject HOME, private 0700 trees, shared 2770 group trees. Idempotent — a tree whose top
    already matches is left alone (cheap steady state). Returns the plan with the shared-workspace
    ``groups`` resolved."""
    root = plan.store_root
    # store root + tier parents: traversable but not listable/enterable across tenants
    for p, mode in ((root, 0o755), (os.path.join(root, ".attached"), 0o711),
                    (os.path.join(root, ".system"), 0o711), (os.path.join(root, ".home"), 0o711)):
        if os.path.isdir(p):
            os.chmod(p, mode)
    # seal EVERY tenant dir, not just this dispatch's — a never-dispatched tenant's data must not
    # sit world-readable while it waits for its owner's first isolated dispatch
    _sweep_default_deny(root)
    # the .attached/<subject> parent dir is the subject's too (their slots live under it)
    subj_attached = os.path.join(root, ".attached", str(plan.uid - UID_BASE))
    private = list(plan.private) + ([subj_attached] if os.path.isdir(subj_attached) else [])
    os.makedirs(plan.home, exist_ok=True)
    private.append(plan.home)
    for path in private:
        if not os.path.isdir(path):
            continue
        st = os.stat(path)
        if st.st_uid != plan.uid or stat.S_IMODE(st.st_mode) != 0o700:
            _chown_tree(path, plan.uid, plan.gid)
            os.chmod(path, 0o700)
    groups: list[int] = []
    for path, ws_id in plan.shared:
        if not os.path.isdir(path):
            continue
        gid = _shared_gid(root, ws_id)
        groups.append(gid)
        st = os.stat(path)
        if st.st_gid != gid or stat.S_IMODE(st.st_mode) != 0o2770:
            _chown_tree(path, 0, gid)
            os.chmod(path, 0o2770)   # setgid: new files inherit the workspace group
    # subscription credentials live under /root — copy them into the subject HOME the worker can read
    creds_src = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.isfile(creds_src):
        dot = os.path.join(plan.home, ".claude")
        os.makedirs(dot, exist_ok=True)
        dst = os.path.join(dot, ".credentials.json")
        try:
            with open(creds_src, "rb") as s, open(dst, "wb") as d:
                d.write(s.read())
            os.chown(dot, plan.uid, plan.gid)
            os.chown(dst, plan.uid, plan.gid)
            os.chmod(dst, 0o400)
        except OSError as e:
            logger.warning("could not stage claude credentials into %s: %s", dot, e)
    return ProcessIsolation(uid=plan.uid, gid=plan.gid, store_root=root, home=plan.home,
                            private=plan.private, shared=plan.shared, groups=tuple(groups))


def preexec_for(plan: ProcessIsolation) -> Callable[[], None]:
    """The Popen ``preexec_fn`` dropping the forked child to the plan's identity (groups → gid → uid,
    in that order — after setuid the process can no longer change groups)."""
    def _drop() -> None:
        os.setgroups(list(plan.groups))
        os.setgid(plan.gid)
        os.setuid(plan.uid)
    return _drop


def child_env_for(plan: ProcessIsolation, env: dict[str, str]) -> dict[str, str]:
    """Env adjustments for the dropped child: a writable per-subject HOME (harness config/session
    files), and TMPDIR under it so scratch files never collide across subjects in /tmp."""
    tmp = os.path.join(plan.home, "tmp")
    os.makedirs(tmp, exist_ok=True)
    os.chown(tmp, plan.uid, plan.gid)
    return {**env, "HOME": plan.home, "TMPDIR": tmp}
