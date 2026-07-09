"""L2: POSIX tenant isolation for the PROCESS backend (lite) — runtime_kernel.isolation.

The plan is PURE (env → uid/gid/dir plan) and fully offline-testable; apply's effects are proven with
recorded chown/chmod calls (a non-root test run cannot chown to arbitrary uids); the gid registry is
real file I/O against tmp_path. The degrade paths must be LOUD and total — a half-applied wall is
worse than an honest shared-trust spawn."""
from __future__ import annotations

import json
import os

import pytest

from runtime_kernel import isolation as iso
from runtime_kernel.isolation import (
    GID_BASE, UID_BASE, ProcessIsolation, apply_process_isolation, plan_process_isolation, preexec_for,
)


def _env(mounts, *, root="/workspaces", subject="17"):
    return {"VEXA_WORKSPACE_MOUNT_TARGET": root, "VEXA_OWNER": subject,
            "VEXA_MOUNTS": json.dumps(mounts)}


MOUNTS = [
    {"slug": "_global", "source": "/srv/g", "path": "/workspaces/_global", "role": "global", "write": False},
    {"slug": "seed", "path": "/workspaces/17", "role": "private", "write": True, "primary": True},
    {"slug": "_system", "path": "/workspaces/.system/17", "role": "system", "write": True},
    {"slug": "deal-9", "path": "/workspaces/deal-9", "role": "shared", "write": True},
]


# ── the plan (pure) ───────────────────────────────────────────────────────────

def test_plan_maps_subject_to_uid_and_buckets_the_mounts():
    plan = plan_process_isolation(_env(MOUNTS), euid=0)
    assert plan is not None
    assert plan.uid == UID_BASE + 17 and plan.gid == plan.uid
    assert plan.private == ("/workspaces/17", "/workspaces/.system/17")   # private + system tiers
    assert plan.shared == (("/workspaces/deal-9", "deal-9"),)             # per-workspace group
    assert plan.home == "/workspaces/.home/17"                            # writable HOME off /root


def test_plan_unavailable_paths_degrade_loudly_to_none(caplog):
    # non-root runtime → None + a WARNING naming the condition
    with caplog.at_level("WARNING"):
        assert plan_process_isolation(_env(MOUNTS), euid=501) is None
    assert "not root" in caplog.text
    # non-numeric subject → None + a WARNING
    caplog.clear()
    with caplog.at_level("WARNING"):
        assert plan_process_isolation(_env(MOUNTS, subject="u_jane"), euid=0) is None
    assert "not numeric" in caplog.text


def test_plan_none_for_workspaceless_workloads():
    """Meeting bots carry no workspace env — nothing to isolate, no warning spam."""
    assert plan_process_isolation({}, euid=0) is None


# ── the gid registry (real file I/O) ─────────────────────────────────────────

def test_shared_gid_allocates_once_and_persists(tmp_path):
    root = str(tmp_path)
    g1 = iso._shared_gid(root, "deal-9")
    g2 = iso._shared_gid(root, "oenb-1424e3")
    assert g1 == GID_BASE and g2 == GID_BASE + 1
    assert iso._shared_gid(root, "deal-9") == g1          # stable on re-ask
    reg = json.loads((tmp_path / iso.GID_REGISTRY).read_text())
    assert reg == {"deal-9": g1, "oenb-1424e3": g2}


# ── apply (effects recorded — a non-root test cannot chown) ───────────────────

def test_apply_chowns_private_trees_and_groups_shared(tmp_path, monkeypatch):
    root = tmp_path
    for d in ("17", ".system/17", ".attached/17", "deal-9", ".attached", ".system"):
        (root / d).mkdir(parents=True, exist_ok=True)
    chowns: list[tuple[str, int, int]] = []
    chmods: list[tuple[str, int]] = []
    monkeypatch.setattr(iso, "_chown_tree", lambda p, u, g: chowns.append((p, u, g)))
    monkeypatch.setattr(os, "chmod", lambda p, m: chmods.append((str(p), m)))
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowns.append((str(p), u, g)))
    plan = ProcessIsolation(uid=UID_BASE + 17, gid=UID_BASE + 17, store_root=str(root),
                            home=str(root / ".home" / "17"),
                            private=(str(root / "17"), str(root / ".system" / "17")),
                            shared=((str(root / "deal-9"), "deal-9"),))
    out = apply_process_isolation(plan)
    # private trees (incl. the subject's .attached parent + HOME) → the subject uid, 0700
    owned = {p for (p, u, g) in chowns if u == UID_BASE + 17}
    assert str(root / "17") in owned and str(root / ".system" / "17") in owned
    assert str(root / ".attached" / "17") in owned
    assert (str(root / "17"), 0o700) in chmods
    # shared tree → root:<allocated gid>, setgid 2770; the gid rides back on the plan
    assert out.groups == (GID_BASE,)
    assert (str(root / "deal-9"), 0, GID_BASE) in chowns
    assert (str(root / "deal-9"), 0o2770) in chmods
    # store root + tier parents: traversal-only for others
    assert (str(root), 0o755) in chmods
    assert (str(root / ".attached"), 0o711) in chmods
    assert (str(root / ".system"), 0o711) in chmods


def test_apply_default_denies_never_dispatched_tenants(tmp_path, monkeypatch):
    """A tenant that NEVER dispatched must not sit world-readable: apply sweeps every tenant dir in the
    store (top-level per tier) to 0700. Already-sealed (0700) and group-managed (2770 shared) dirs are
    left alone; special dirs are skipped."""
    root = tmp_path
    for d in ("17", "9", ".system/9", ".attached/9", "deal-9", "_global", ".home"):
        (root / d).mkdir(parents=True, exist_ok=True)
    os.chmod(root / "9", 0o755)                       # never-dispatched tenant — open
    os.chmod(root / ".system" / "9", 0o755)
    monkeypatch.setattr(iso, "_chown_tree", lambda p, u, g: None)
    monkeypatch.setattr(os, "chown", lambda p, u, g: None)
    plan = ProcessIsolation(uid=UID_BASE + 17, gid=UID_BASE + 17, store_root=str(root),
                            home=str(root / ".home" / "17"), private=(str(root / "17"),), shared=())
    apply_process_isolation(plan)
    assert (os.stat(root / "9").st_mode & 0o777) == 0o700          # sealed
    assert (os.stat(root / ".system" / "9").st_mode & 0o777) == 0o700
    assert (os.stat(root / ".attached" / "9").st_mode & 0o777) == 0o700
    assert (os.stat(root / "_global").st_mode & 0o777) != 0o700    # special dir untouched by the sweep


def test_preexec_drops_groups_then_gid_then_uid(monkeypatch):
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append(("groups", tuple(g))))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append(("gid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append(("uid", u)))
    plan = ProcessIsolation(uid=100017, gid=100017, store_root="/w", home="/w/.home/17",
                            groups=(200000,))
    preexec_for(plan)()
    assert calls == [("groups", (200000,)), ("gid", 100017), ("uid", 100017)]  # order matters post-setuid
