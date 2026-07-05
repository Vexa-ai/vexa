"""The two SYSTEM TIERS of the three-tier mount stack (AMENDMENT 4): the _global GLOBAL SYSTEM tier
(read-only, mount-when-present) and the _system PRIVATE SYSTEM tier (read-write, create-if-absent), and
their composition by ``dispatch.build_mount_set`` into the full stack ``[_global?, *active, _system]``.

Backend-free (fakes / tmp dirs, no docker/kubectl) — the system-mount plumbing is proven offline."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from control_plane import system_mounts
from control_plane.dispatch import build_active_set, build_mount_set
from control_plane.system_mounts import ensure_system_workspace, global_mount, system_mount
from shared.config import load_settings


def _git_repo(d: Path, marker: str = "X") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (d / "CLAUDE.md").write_text(marker); run("add", "-A"); run("commit", "-q", "-m", "seed")
    return d


def _seed_baseline(root: Path, subject: str) -> Path:
    """A seeded private baseline at <root>/<subject> (the active-set primary)."""
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("SEED")
    seed_workspace(ws, None)
    return ws


# ── _global: GLOBAL SYSTEM tier — READ-ONLY, mount-when-present ────────────────

def test_global_mount_is_read_only_with_its_own_source(tmp_path):
    gdir = _git_repo(tmp_path / "global", "GLOBAL")
    settings = load_settings(global_system_workspace_path=str(gdir))
    m = global_mount(settings, "/workspaces")
    assert m is not None
    assert m["slug"] == "_global" and m["role"] == "global"
    assert m["write"] is False                       # agents NEVER write _global
    assert m["source"] == str(gdir)                  # the platform host repo (distinct bind source)
    assert m["path"] == "/workspaces/_global"        # bound read-only at the reserved container path


def test_global_mount_carries_a_pinned_ref(tmp_path):
    gdir = _git_repo(tmp_path / "global")
    settings = load_settings(global_system_workspace_path=str(gdir), global_system_workspace_ref="v1.2.3")
    assert global_mount(settings, "/workspaces")["ref"] == "v1.2.3"
    # unpinned → mount HEAD (ref is None)
    settings2 = load_settings(global_system_workspace_path=str(gdir))
    assert global_mount(settings2, "/workspaces")["ref"] is None


def test_global_mount_absent_when_unconfigured_or_missing(tmp_path):
    # unconfigured → skip (None), the stack degrades gracefully
    assert global_mount(load_settings(), "/workspaces") is None
    # configured but the path does not exist → skip (None) + logged, never raises
    settings = load_settings(global_system_workspace_path=str(tmp_path / "does-not-exist"))
    assert global_mount(settings, "/workspaces") is None


# ── _system: PRIVATE SYSTEM tier — READ-WRITE, create-if-absent ───────────────

def test_system_workspace_is_created_if_absent_as_a_git_repo(tmp_path):
    home = ensure_system_workspace(str(tmp_path / "ws"), "u1")
    assert home == tmp_path / "ws" / ".system" / "u1"
    assert (home / ".git").exists() and (home / "README.md").exists()  # thin template, committed
    # HEAD exists so a turn can commit onto it
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(home), capture_output=True, text=True)
    assert head.returncode == 0 and head.stdout.strip()


def test_system_workspace_create_if_absent_is_idempotent(tmp_path):
    root = str(tmp_path / "ws")
    home1 = ensure_system_workspace(root, "u1")
    (home1 / "settings.json").write_text("{}")   # a later WP writes here; must survive re-ensure
    home2 = ensure_system_workspace(root, "u1")
    assert home1 == home2 and (home2 / "settings.json").read_text() == "{}"  # untouched, not re-seeded


def test_system_mount_is_read_write_and_always_present(tmp_path):
    m = system_mount(str(tmp_path / "ws"), "u1")
    assert m["slug"] == "_system" and m["role"] == "system"
    assert m["write"] is True and m["primary"] is False
    assert m["path"] == str(tmp_path / "ws" / ".system" / "u1")  # in-store → rides the store-root bind


# ── composition: the full three-tier stack via build_mount_set ────────────────

def test_build_mount_set_is_global_active_system_in_order(tmp_path):
    root = tmp_path / "ws"
    _seed_baseline(root, "u1")
    gdir = _git_repo(tmp_path / "global", "GLOBAL")
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(gdir))

    stack = build_mount_set(settings, "u1")
    assert [m["role"] for m in stack] == ["global", "private", "system"]
    assert stack[0]["write"] is False and stack[-1]["write"] is True     # _global RO, _system RW
    # the middle tier IS exactly the active set
    active = build_active_set(settings, "u1")
    assert [m["slug"] for m in stack if m["role"] == "private"] == [m["slug"] for m in active]


def test_build_mount_set_degrades_without_global(tmp_path):
    """No _global configured → the stack is [*active, _system] (system tiers fail soft into the set)."""
    root = tmp_path / "ws"
    _seed_baseline(root, "u1")
    settings = load_settings(workspaces_dir=str(root))
    stack = build_mount_set(settings, "u1")
    assert [m["role"] for m in stack] == ["private", "system"]  # _global skipped, _system always present


def test_build_mount_set_survives_a_broken_system_tier(tmp_path, monkeypatch):
    """A failure creating _system must NOT abort the dispatch — it degrades to just the active set + logs."""
    root = tmp_path / "ws"
    _seed_baseline(root, "u1")
    settings = load_settings(workspaces_dir=str(root))

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(system_mounts, "system_mount", boom)
    # build_mount_set imports system_mount at module load; patch it there too
    import control_plane.dispatch as disp
    monkeypatch.setattr(disp, "system_mount", boom)

    stack = build_mount_set(settings, "u1")
    assert [m["role"] for m in stack] == ["private"]  # ran without _system, never raised
