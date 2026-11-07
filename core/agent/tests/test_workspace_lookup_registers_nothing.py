"""A slug a request names is looked up, never registered.

Two people. 126 keeps a private workspace in the attach store and a private system tier; 127 names
the platform's own directories at the store root (``.attached``, ``.system``) as workspaces, through
the two lookups that take a slug (the identity read by slug and the link resolver's "here"). Neither
lookup may turn such a directory into a workspace: no record, no id file written into it, and no
read of 126's files through it. A workspace that already has a record is still found.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import workspace_ids as ids
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from workspaces.shared.workspace_id import mint_id


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True, check=True)


def _init_ws(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return path


def _world(root: Path):
    _init_ws(root / "126")
    _init_ws(root / "127")
    secret = root / ".attached" / "126" / "private-notes"
    secret.mkdir(parents=True)
    (secret / "plan.md").write_text("126's private plan\n")
    system = root / ".system" / "126"
    system.mkdir(parents=True)
    (system / "identity.md").write_text("126's private identity\n")
    client = TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
                                   reader=WorkspaceReader(str(root)),
                                   membership_index=m.InMemoryMembershipIndex()))
    return client, client.app


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


def test_naming_a_platform_directory_by_slug_registers_nothing(tmp_path):
    client, _ = _world(tmp_path)
    for name in (".attached", ".system"):
        r = client.get(f"/api/workspaces/by-slug/{name}", headers=_h("127"))
        assert r.status_code == 200
        assert r.json()["access"] == ids.ACCESS_GONE
        assert not (tmp_path / name / ".vexa").exists()          # no id file written into it


def test_naming_a_platform_directory_as_here_registers_nothing(tmp_path):
    client, _ = _world(tmp_path)
    for name in (".attached", ".system"):
        out = client.post("/api/links/resolve", headers=_h("127"),
                          json={"refs": ["plan"], "slug": name}).json()["results"]
        assert out[0]["access"] == ids.ACCESS_GONE and out[0]["url"] is None
        assert not (tmp_path / name / ".vexa").exists()


def test_another_persons_private_files_are_not_read_through_a_named_platform_directory(tmp_path):
    client, _ = _world(tmp_path)
    for name, path, secret in ((".attached", "126/private-notes/plan.md", "private plan"),
                               (".system", "126/identity.md", "private identity")):
        client.get(f"/api/workspaces/by-slug/{name}", headers=_h("127"))
        client.post("/api/links/resolve", headers=_h("127"), json={"refs": ["x"], "slug": name})
        r = client.get(f"/api/workspace/file?path={path}&slug={name}", headers=_h("127"))
        assert r.status_code in (403, 404)
        assert secret not in r.text


def test_a_record_left_by_a_registered_platform_directory_is_gone_and_dropped(tmp_path):
    reg = ids.WorkspaceRegistry()
    (tmp_path / ".system").mkdir()
    poisoned = reg.put({"id": mint_id(), "slug": ".system", "dir": str(tmp_path / ".system"),
                        "kind": "desk", "name": "Desk .system", "owner": ".system"})
    assert ids.access_for(poisoned, "127", root=tmp_path) == ids.ACCESS_GONE
    out = ids.migrate(tmp_path, reg)
    assert poisoned["id"] in out["dropped"] and reg.get(poisoned["id"]) is None


def test_a_known_workspace_is_still_found_by_slug(tmp_path):
    client, _ = _world(tmp_path)
    r = client.get("/api/workspaces/by-slug/126", headers=_h("127"))
    assert r.status_code == 200 and r.json()["access"] == ids.ACCESS_READABLE


def test_a_lookup_re_points_a_moved_workspace_and_registers_no_new_one(tmp_path):
    reg = ids.WorkspaceRegistry()
    _init_ws(tmp_path / "grp")
    rec = ids.sync_workspace(tmp_path, "grp", registry=reg)
    (tmp_path / "grp").rename(tmp_path / "grp-renamed")
    moved = ids.resolve_slug(tmp_path, "grp-renamed", registry=reg)
    assert moved["id"] == rec["id"] and moved["slug"] == "grp-renamed"
    _init_ws(tmp_path / "fresh")                              # a tree with no id yet
    assert ids.resolve_slug(tmp_path, "fresh", registry=reg) is None
    assert not (tmp_path / "fresh" / ".vexa").exists()
