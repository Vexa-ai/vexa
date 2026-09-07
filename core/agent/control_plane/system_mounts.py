"""system_mounts.py — the two SYSTEM TIERS of the three-tier mount stack (AMENDMENT 4).

Every agent worker turn mounts a STACK, not one workspace. The stack is a LIST (never special-cased
slots) with three tiers, and this module owns the two system tiers that bracket the subject's normal
active set (the ``_global`` read-only base and the ``_system`` per-user private base):

  1. ``/workspaces/_global``  GLOBAL SYSTEM — platform-owned, READ-ONLY, ALWAYS mounted into EVERY worker.
        Behaviour (CLAUDE.md-level instructions), shared skills, common tools, base knowledge. Agents
        never write it (the mount is ``write=False`` → the runtime binds it ``:ro``). A LIVE MOUNT, not a
        seed: updating the one _global repo propagates to all agents next turn. Source = an env-configured
        path (``GLOBAL_SYSTEM_WORKSPACE_PATH``, config.v1-declared). Mount HEAD; a pinned ref is supported
        via ``GLOBAL_SYSTEM_WORKSPACE_REF`` (default the repo's HEAD/main). Missing configuration fails
        closed: no worker may run without the organisation tier.

  2. ``/workspaces/_system``  PRIVATE SYSTEM — per-user, READ-WRITE, ALWAYS mounted. Chats/sessions,
        settings, routines, membership/attachment records, credential refs. Private, never shareable.
        CREATE-IF-ABSENT from a THIN template (layout only; behaviour lives in _global). Chats MIGRATE
        here in a LATER WP — this WP only establishes the mount.

The normal active set (WP-A2.1: the subject's private baseline + activated extras) sits BETWEEN these as
the middle tier; ``dispatch.build_mount_set`` composes the full stack ``[_global, *active, _system]``.

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

# The LIGHT self-identity reference the _system tier ships with. It is deliberately minimal — just enough
# for the agent to know who it's helping on EVERY turn (even when the Personal workspace is switched off).
# The FULL profile (company, role, relationships, history) lives in the user's Personal workspace as the
# `self: true` person entity; this file links to it. The agent fills `name` by asking the user (and keeps
# asking until it's set — see worker/engine.py mounts_preamble).
_IDENTITY_STUB = (
    "# Who you're helping\n\n"
    "Your LIGHT, always-available reference for who the user is. It lives in the private system\n"
    "workspace so you know who you're talking to on every turn — even when the user's Personal\n"
    "workspace is switched off. Keep it to the essentials; the FULL profile (company, role,\n"
    "relationships, history) lives in the user's **Personal** workspace as the `self: true` person\n"
    "entity under `kg/entities/person/`. Link to it from here once it exists.\n\n"
    "## User\n\n"
    "- **name:** _(unknown — ask the user, then record it here)_\n"
    "- **personal profile:** _(link to the `self: true` person entity in Personal once created)_\n\n"
    "> If **name** is still unknown, ask the user their name early and record it here. It is the one\n"
    "> fact you should not leave blank — everything else can be researched or deferred.\n"
)


def global_mount(settings, root: str) -> dict:
    """The mandatory GLOBAL SYSTEM tier (``_global``), mounted into every worker.

    Source is the platform-operated _global repo/dir named by ``settings.global_system_workspace_path``
    (env ``GLOBAL_SYSTEM_WORKSPACE_PATH``). ``path`` is that source bound at ``<root>/_global`` — an
    OUT-OF-STORE mount, so the runtime gives it its OWN read-only bind (source→target). Mount HEAD; a
    pinned ref (``GLOBAL_SYSTEM_WORKSPACE_REF``) is carried through as the mount ``ref`` for the backend
    to check out on materialization (default: whatever the repo's HEAD is)."""
    src = (getattr(settings, "global_system_workspace_path", "") or "").strip()
    if not src:
        raise RuntimeError("VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH is required: every agent stack includes _global")
    if not Path(src).exists():
        raise RuntimeError(f"VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH does not exist: {src}")
    if not Path(src).is_dir():
        raise RuntimeError(f"VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH is not a directory: {src}")
    ref = (getattr(settings, "global_system_workspace_ref", "") or "").strip() or None
    target = f"{root}/{GLOBAL_SLUG}"
    mount = {
        "slug": GLOBAL_SLUG,
        "path": target,                      # where it lands in the worker (the bind TARGET)
        "ref": ref,                          # pinned ref, or None = mount HEAD
        "role": "global",
        "write": False,                      # READ-ONLY by default — the admin session flips this
        "primary": False,
    }

    # ── ONE STORE, OR THE WRITE GOES SOMEWHERE NOBODY READS ──────────────────────────────────────
    # `source` on a mount means "a host path to bind from", and it is resolved by the DOCKER DAEMON
    # ON THE HOST. Every other value here is resolved by agent-api INSIDE ITS CONTAINER. When
    # `_global` lives in the workspace store, those are two different filesystems wearing the same
    # string, and emitting `source` picks the wrong one.
    #
    # ⚠ THE FAILURE THIS PREVENTS, 2026-09-02, live, on the founder's own first-run setup chat.
    # `VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH=/workspaces/_global` passed the existence check above,
    # because inside agent-api that path IS the store volume. It was then handed to docker as a
    # bind source, where `/workspaces/_global` is a host directory that did not exist — so docker
    # AUTO-CREATED an empty one and mounted it read-write. The admin dictated the company layer,
    # the agent wrote README.md, the write SUCCEEDED, and the agent truthfully said so. It had
    # written into a phantom store that no reader of `_global` has ever looked at: the gate saw no
    # README, the setup could not complete, and from the outside it read as an agent fabricating a
    # write it never made. It was the opposite — a write nobody could see. Two disjoint stores
    # behind one name is the whole defect, and it fails silently by construction, because both
    # halves report success.
    #
    # THE FIX IS TO STOP HAVING TWO. When `_global` sits inside the store root, emit NO source: the
    # runtime then binds it out of the store volume by subpath, exactly the way `/workspaces/57`
    # and `_system` are already bound (runtime_kernel.mounts.workspace_binds). One store, resolved
    # once, by the component that owns it — and it is the same bytes agent-api reads, by
    # construction rather than by a check that can drift.
    #
    # A `_global` genuinely OUTSIDE the store (a separately-managed repo on the host) keeps its own
    # source, which is what that branch is for. It is a real deployment shape and it is not the one
    # that broke: an out-of-store path means the same thing to agent-api and to the daemon.
    src_p = Path(src).resolve()
    root_p = Path(root).resolve()
    if src_p == root_p / GLOBAL_SLUG or (root_p in src_p.parents and src_p.name == GLOBAL_SLUG):
        return mount                          # in-store: rides the store bind, no source
    if root_p in src_p.parents:
        # In-store but under some OTHER name. The runtime derives the store subpath from `path`,
        # which is always `<root>/_global`, so honouring this would mount a different directory
        # than the operator named — silently, and in the same class as the bug above.
        raise RuntimeError(
            f"VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH points inside the workspace store but is not "
            f"{root}/{GLOBAL_SLUG}: {src}. Inside the store the organisation tier must BE "
            f"{GLOBAL_SLUG}; point it outside the store to manage it separately.")
    mount["source"] = src                     # out-of-store: its own bind, source -> target
    return mount


def _system_store(root: Path, subject: str) -> Path:
    """The on-disk home of the subject's private-system workspace — under the store's reserved
    ``.system`` namespace, keyed by subject, so it rides the single store-root bind like the baseline."""
    return root / SYSTEM_STORE_DIRNAME / subject


def system_store_path(root: str | Path, subject: str) -> Path:
    """Public accessor for the caller's OWN private-system workspace path — so the read API can surface
    ``_system`` (RW, hidden-by-default) in the files panel without exposing the store layout."""
    return _system_store(Path(root), subject)


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
        # The thin template: a marker + the LIGHT identity reference so the repo is non-empty and the
        # agent always knows who it is helping. Chats/sessions, settings, routines, membership records
        # land here in later WPs.
        (home / "README.md").write_text(
            "# Private system workspace\n\n"
            "Per-user, read-write, always mounted. Holds who you're helping (`identity.md`),\n"
            "chats/sessions, settings, routines, membership/attachment records, and credential refs.\n"
            "Private — never shareable.\n"
        )
        (home / "identity.md").write_text(_IDENTITY_STUB)
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
