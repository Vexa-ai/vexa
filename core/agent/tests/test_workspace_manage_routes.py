"""The workspace MANAGE routes — git-remote-status, push, pull, purpose — wired over _manage_dir.

Proves the HTTP surface resolves the caller's own (primary) workspace by path, gates on the header
identity (P20), and round-trips purpose + reports git sync state. Real git, local remotes, no network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _client(root: Path) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
    ))


def _seed_primary(root: Path, subject: str, *, with_origin: bool = False) -> Path:
    """The subject's primary workspace lives at <root>/<subject> (the seed slot)."""
    ws = root / subject
    if with_origin:
        bare = root / "remote.git"; bare.mkdir(parents=True)
        _run(bare, "init", "-q", "--bare", "-b", "main")
        subprocess.run(["git", "clone", "-q", str(bare), str(ws)], check=True, capture_output=True, text=True)
        _run(ws, "config", "user.email", "t@t"); _run(ws, "config", "user.name", "t")
        (ws / "README.md").write_text("v0\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "init")
        _run(ws, "push", "-q", "origin", "main")
    else:
        ws.mkdir(parents=True)
        _run(ws, "init", "-q", "-b", "main"); _run(ws, "config", "user.email", "t@t"); _run(ws, "config", "user.name", "t")
        (ws / "README.md").write_text("v0\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "init")
    return ws


H = {"X-User-Id": "u_jane"}


def test_git_remote_status_no_home(tmp_path):
    _seed_primary(tmp_path, "u_jane", with_origin=False)
    c = _client(tmp_path)
    r = c.get("/api/workspace/git-remote-status", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["has_home"] is False and body["branch"] == "main"


def test_git_remote_status_reports_ahead_after_a_local_commit(tmp_path):
    ws = _seed_primary(tmp_path, "u_jane", with_origin=True)
    (ws / "note.md").write_text("local\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "local")
    c = _client(tmp_path)
    body = c.get("/api/workspace/git-remote-status", headers=H).json()
    assert body["has_home"] is True and body["remote"] == "origin"
    assert body["ahead"] == 1 and body["behind"] == 0 and body["tracked"] is True


def test_manage_is_scoped_to_the_header_identity(tmp_path):
    """Each caller manages only THEIR OWN workspace — purpose set by u_jane is invisible to u_bob (P20)."""
    _seed_primary(tmp_path, "u_jane")
    _seed_primary(tmp_path, "u_bob")
    c = _client(tmp_path)
    c.post("/api/workspace/purpose", headers=H, json={"purpose": "jane's deal room"})
    assert c.get("/api/workspace/purpose", headers={"X-User-Id": "u_bob"}).json()["purpose"] == ""


def test_purpose_roundtrip_via_routes(tmp_path):
    _seed_primary(tmp_path, "u_jane")
    c = _client(tmp_path)
    assert c.get("/api/workspace/purpose", headers=H).json()["purpose"] == ""
    r = c.post("/api/workspace/purpose", headers=H, json={"purpose": "  ACME deal room  "})
    assert r.status_code == 200 and r.json()["purpose"] == "ACME deal room"
    assert c.get("/api/workspace/purpose", headers=H).json()["purpose"] == "ACME deal room"


def test_push_via_route_fast_forwards(tmp_path):
    ws = _seed_primary(tmp_path, "u_jane", with_origin=True)
    (ws / "note.md").write_text("local\n"); _run(ws, "add", "-A"); sha = None
    _run(ws, "commit", "-q", "-m", "local"); sha = _run(ws, "rev-parse", "HEAD")
    c = _client(tmp_path)
    r = c.post("/api/workspace/push", headers=H, json={"token": "ghp_x"})
    assert r.status_code == 200, r.text
    assert r.json()["head_sha"] == sha
    # status now in sync
    body = c.get("/api/workspace/git-remote-status", headers=H).json()
    assert body["ahead"] == 0
