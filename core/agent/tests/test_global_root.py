"""ONE ANSWER TO "WHERE IS `_global`?" — the writers, the readers and the worker mount agree (S9).

Seven places used to decide it. The mount and the boot took the configured
`VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH` first; the admin's file editor, the page writer, reset, the
ready-commit and the preset reader took the in-store `<workspaces_dir>/_global` first whenever that
directory existed. On an instance that once ran with the default it does exist, so with an
out-of-store path configured the admin's edits landed in a `_global` no worker mounts — the
2026-09-02 "two disjoint stores" defect, back. Now `system_mounts.global_root` is the one answer.

Both modes are pinned: no configured path (the in-store `_global`), and an out-of-store path with a
stale in-store `_global` lying next to it — the case that split.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import system_mounts
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests.test_api import _FakeIdentity, _FakeRuntime

ADMIN = "u_admin"
AGENT = Path(__file__).resolve().parents[1]


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


def _settings(store: Path, configured: str):
    return load_settings(workspaces_dir=str(store), global_system_workspace_path=configured,
                         global_admin_subjects=ADMIN, internal_api_secret="s",
                         ui_url="https://app.example.test", redis_url="")


def _client(store: Path, settings) -> TestClient:
    return TestClient(create_app(
        Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(store)),
        membership_index=m.InMemoryMembershipIndex(),
    ))


@pytest.fixture(params=["in-store", "out-of-store"])
def deployment(request, tmp_path):
    """(store, settings, the `_global` every party must use, the one nobody may touch)."""
    store = tmp_path / "workspaces"
    store.mkdir()
    in_store = _repo(store / system_mounts.GLOBAL_SLUG)
    if request.param == "in-store":
        return store, _settings(store, ""), in_store, None
    outside = _repo(tmp_path / "operator-global")
    # The in-store slot an earlier default run left behind: present, writable, and NOT the one.
    return store, _settings(store, str(outside)), outside, in_store


def test_the_resolver_names_one_directory(deployment):
    store, settings, want, _stale = deployment
    assert system_mounts.global_root(settings) == want
    assert system_mounts.global_root(settings, store) == want


def test_a_configured_path_that_is_the_in_store_slot_is_the_in_store_slot(tmp_path):
    settings = _settings(tmp_path, str(tmp_path / system_mounts.GLOBAL_SLUG))
    assert system_mounts.global_root(settings) == tmp_path / system_mounts.GLOBAL_SLUG


def test_the_worker_mount_serves_the_resolved_directory(deployment):
    store, settings, want, _stale = deployment
    mount = system_mounts.global_mount(settings, str(store))
    assert Path(mount.get("source") or mount["path"]).resolve() == want.resolve()


def test_the_admin_editor_writes_where_the_mount_reads_and_the_reader_reads_it_back(deployment):
    store, settings, want, stale = deployment
    c = _client(store, settings)
    h = {"X-User-Id": ADMIN}
    r = c.put("/api/workspace/file", headers=h,
              json={"path": "PRINCIPLES.md", "content": "# How we work\n", "slug": "_global"})
    assert r.status_code == 200, r.text
    assert (want / "PRINCIPLES.md").read_text() == "# How we work\n"
    if stale is not None:
        assert not (stale / "PRINCIPLES.md").exists(), "the edit went to a _global no worker mounts"
    r = c.get("/api/workspace/file", headers=h, params={"path": "PRINCIPLES.md", "slug": "_global"})
    assert r.status_code == 200 and "How we work" in r.text


def test_the_page_writer_writes_where_the_mount_reads(deployment):
    store, settings, want, stale = deployment
    c = _client(store, settings)
    r = c.post("/api/workspace/entity", headers={"X-User-Id": ADMIN},
               json={"slug": "_global", "kind": "company", "name": "Acme",
                     "facts": ["Acme sells widgets."], "source": "the admin"})
    assert r.status_code == 200, r.text
    written = [p for p in want.rglob("*.md") if "acme" in p.name]
    assert written, "the company page is not in the _global the mount serves"
    if stale is not None:
        assert not [p for p in stale.rglob("*.md") if "acme" in p.name]


def test_reset_resets_the_resolved_directory(deployment, tmp_path, monkeypatch):
    store, settings, want, stale = deployment
    seeds = tmp_path / "seeds" / "default"
    seeds.mkdir(parents=True)
    (seeds / "CLAUDE.md").write_text("# seed\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEEDS_DIR", str(seeds.parent))
    (want / "doomed.md").write_text("x\n")
    if stale is not None:
        (stale / "kept.md").write_text("x\n")
    r = _client(store, settings).post("/api/workspace/reset", headers={"X-User-Id": ADMIN},
                                      json={"target": "_global"})
    assert r.status_code == 200, r.text
    assert not (want / "doomed.md").exists()
    if stale is not None:
        assert (stale / "kept.md").exists(), "reset reached a _global it does not serve"


def test_no_module_decides_where_global_is_on_its_own():
    """Every reader and writer asks `system_mounts.global_root`; nothing else reads the setting."""
    offenders = []
    for path in (AGENT / "control_plane").rglob("*.py"):
        if path.name == "system_mounts.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"settings\.global_system_workspace_path|[\"']VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH[\"']", text):
            offenders.append(path.relative_to(AGENT).as_posix())
    assert offenders == []
