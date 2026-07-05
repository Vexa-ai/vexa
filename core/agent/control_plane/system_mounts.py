"""system_mounts.py — the two SYSTEM TIERS of the three-tier mount stack (AMENDMENT 4).

Every agent worker turn mounts a STACK, not one workspace. The stack is a LIST (never special-cased
slots) with three tiers, and this module owns the two system tiers that bracket the subject's normal
active set (the ``_global`` read-only base and the ``_system`` per-user private base):

  1. ``/workspaces/_global``  GLOBAL SYSTEM — platform-owned, READ-ONLY, ALWAYS mounted into EVERY worker.
        Behaviour (CLAUDE.md-level instructions), shared skills, common tools, base knowledge. Agents
        never write it (the mount is ``write=False`` → the runtime binds it ``:ro``). A LIVE MOUNT, not a
        seed: updating the one _global repo propagates to all agents next turn. Source = an env-configured
        path (``GLOBAL_SYSTEM_WORKSPACE_PATH``, config.v1-declared). Mount HEAD; a pinned ref is supported
        via ``GLOBAL_SYSTEM_WORKSPACE_REF`` (default the repo's HEAD/main). ABSENT (unconfigured, or the
        configured path does not exist) → SKIP the mount and log; the stack degrades to _system + active.

  2. ``/workspaces/_system``  PRIVATE SYSTEM — per-user, READ-WRITE, ALWAYS mounted. Chats/sessions,
        settings, routines, membership/attachment records, credential refs. Private, never shareable.
        CREATE-IF-ABSENT from a THIN template (layout only; behaviour lives in _global). Chats MIGRATE
        here in a LATER WP — this WP only establishes the mount.

The normal active set (WP-A2.1: the subject's private baseline + activated extras) sits BETWEEN these as
the middle tier; ``dispatch.build_mount_set`` composes the full stack ``[_global?, *active, _system]``.

Pure + path-driven so the mount stack is unit-tested offline (no docker/kubectl): both builders take a
``workspaces_dir`` root + settings-like knobs and return the ``{slug, path, role, write, primary}`` mount
dicts the runtime backends already understand (an out-of-store ``_global`` path becomes its own bind; the
in-store ``_system`` path rides the store-root bind — see ``runtime_kernel.mounts``).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from shared.gitenv import scrubbed_git_env

logger = logging.getLogger("agent_api.system_mounts")

# The two system tiers' reserved container paths + slugs. They are dot-reserved (``_``-prefixed) so they
# never collide with a normal workspace slug and are visually distinct in the harness preamble.
GLOBAL_SLUG = "_global"
SYSTEM_SLUG = "_system"

# The per-user private-system store lives under the store's reserved namespace, keyed by subject, so it
# rides the single store-root bind (no extra bind) exactly like the private baseline.
SYSTEM_STORE_DIRNAME = ".system"


def global_mount(settings, root: str) -> Optional[dict]:
    """The GLOBAL SYSTEM tier (``_global``) — READ-ONLY, mounted into every worker when configured and
    present. Returns the mount dict, or None (SKIP + log) when the knob is unset or the path is absent.

    Source is the platform-operated _global repo/dir named by ``settings.global_system_workspace_path``
    (env ``GLOBAL_SYSTEM_WORKSPACE_PATH``). ``path`` is that source bound at ``<root>/_global`` — an
    OUT-OF-STORE mount, so the runtime gives it its OWN read-only bind (source→target). Mount HEAD; a
    pinned ref (``GLOBAL_SYSTEM_WORKSPACE_REF``) is carried through as the mount ``ref`` for the backend
    to check out on materialization (default: whatever the repo's HEAD is)."""
    src = (getattr(settings, "global_system_workspace_path", "") or "").strip()
    if not src:
        logger.info("no GLOBAL_SYSTEM_WORKSPACE_PATH configured — skipping the _global system mount")
        return None
    if not Path(src).exists():
        logger.warning("GLOBAL_SYSTEM_WORKSPACE_PATH=%s does not exist — skipping the _global system mount", src)
        return None
    ref = (getattr(settings, "global_system_workspace_ref", "") or "").strip() or None
    return {
        "slug": GLOBAL_SLUG,
        "source": src,                       # the platform-owned repo/dir on the host (the bind SOURCE)
        "path": f"{root}/{GLOBAL_SLUG}",     # where it lands in the worker (the bind TARGET)
        "ref": ref,                          # pinned ref, or None = mount HEAD
        "role": "global",
        "write": False,                      # READ-ONLY — agents never write _global (bound :ro)
        "primary": False,
    }


def _system_store(root: Path, subject: str) -> Path:
    """The on-disk home of the subject's private-system workspace — under the store's reserved
    ``.system`` namespace, keyed by subject, so it rides the single store-root bind like the baseline."""
    return root / SYSTEM_STORE_DIRNAME / subject


def ensure_system_workspace(root: str, subject: str, *, seed_dir: Optional[Path] = None) -> Path:
    """CREATE-IF-ABSENT the subject's PRIVATE SYSTEM workspace and return its on-disk path. Idempotent:
    an existing ``.system/<subject>`` is returned untouched. Materialized from a THIN template (layout
    only — behaviour lives in _global): when ``seed_dir`` is given its tree is copied; otherwise a bare
    git repo with a single ``README`` marker is created. Either way it ends as a git repo with a HEAD so
    a turn can commit onto it (chats migrate here in a later WP)."""
    home = _system_store(Path(root), subject)
    if (home / ".git").exists():
        return home
    home.mkdir(parents=True, exist_ok=True)
    if seed_dir and Path(seed_dir).exists():
        import shutil
        for item in Path(seed_dir).iterdir():
            dst = home / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
    else:
        # The thin template: a single marker so the repo is non-empty + self-describing. Chats/sessions,
        # settings, routines, membership records land here in later WPs.
        (home / "README.md").write_text(
            "# Private system workspace\n\n"
            "Per-user, read-write, always mounted. Holds chats/sessions, settings, routines,\n"
            "membership/attachment records, and credential refs. Private — never shareable.\n"
        )
    env = scrubbed_git_env()
    for args in (("init", "-q"), ("config", "user.email", "agent@vexa"), ("config", "user.name", "vexa-agent")):
        subprocess.run(["git", *args], cwd=str(home), check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=str(home), check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "system workspace init", "--allow-empty"],
                   cwd=str(home), check=True, capture_output=True, text=True, env=env)
    return home


def system_mount(root: str, subject: str, *, seed_dir: Optional[Path] = None) -> dict:
    """The PRIVATE SYSTEM tier (``_system``) — READ-WRITE, ALWAYS mounted. Ensures the workspace exists
    (create-if-absent), then returns its mount dict at ``<root>/_system``. Its on-disk home is under the
    store's ``.system/<subject>`` namespace (an IN-STORE path → rides the store-root bind, no extra bind);
    ``path`` is that home so the runtime binds the bytes and the harness declares the container path.

    Note the container-facing path is the store home itself (``.system/<subject>``); the worker sees it as
    the ``_system`` tier via the mount's slug/role, not via a separate rebind (the store root already
    exposes it). Fails SOFT is NOT applied here: _system is REQUIRED, so a failure to create it raises."""
    home = ensure_system_workspace(root, subject, seed_dir=seed_dir)
    return {
        "slug": SYSTEM_SLUG,
        "path": str(home),
        "role": "system",
        "write": True,
        "primary": False,
    }
