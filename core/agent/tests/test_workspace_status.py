"""workspace_status — look at a workspace's GitHub home without changing the workspace.

The agent's only GitHub-reaching verbs used to be `workspace_pull` (fetch AND fast-forward — it moves
the branch) and `workspace_push`. A person asking "is this ahead or behind GitHub?" had no answer that
did not also change something, and the agent's own sandbox cannot run git in the workspace. This verb
is the look-only half: the platform fetches with the workspace's credential, moves ONLY the
remote-tracking ref, and reports what differs.

Real git over local repos throughout; nothing touches a network.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import route_policy
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_git_sync import SyncRefused, fetch_status, push_origin
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests import gitserve

AGENT = Path(__file__).resolve().parents[1]
TOKEN = "ghp_SECRET_token_123"


@pytest.fixture(autouse=True)
def _local_remotes_opted_in(monkeypatch, tmp_path):
    from control_plane.repo_ref import LOCAL_ROOTS_ENV
    monkeypatch.setenv(LOCAL_ROOTS_ENV, str(tmp_path))


def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _bare(path: Path) -> Path:
    path.mkdir(parents=True)
    _run(path, "init", "-q", "--bare", "-b", "main")
    return path


def _clone(remote: Path, dest: Path, *, seed: bool = False) -> Path:
    if seed:
        tmp = dest.parent / (dest.name + "_seed")
        subprocess.run(["git", "clone", "-q", str(remote), str(tmp)], check=True, capture_output=True)
        _run(tmp, "config", "user.email", "t@t"); _run(tmp, "config", "user.name", "t")
        (tmp / "README.md").write_text("v0\n")
        _run(tmp, "add", "-A"); _run(tmp, "commit", "-q", "-m", "init"); _run(tmp, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    _run(dest, "config", "user.email", "t@t"); _run(dest, "config", "user.name", "t")
    return dest


def _commit(ws: Path, subject: str, **files: str) -> str:
    for rel, body in (files or {"note.md": subject}).items():
        (ws / rel).write_text(body)
    _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", subject)
    return _run(ws, "rev-parse", "HEAD")


def _snapshot(ws: Path) -> dict:
    """Everything a person could see change: the branch, where it points, the index and the tree."""
    return {
        "branch": _run(ws, "rev-parse", "--abbrev-ref", "HEAD"),
        "head": _run(ws, "rev-parse", "HEAD"),
        "status": _run(ws, "status", "--porcelain=v1", "--untracked-files=all"),
        "files": sorted((str(p.relative_to(ws)), p.read_bytes()) for p in ws.rglob("*")
                        if p.is_file() and ".git" not in p.relative_to(ws).parts),
    }


# ── the mechanic ────────────────────────────────────────────────────────────────────────────────

def test_status_fetches_and_reports_both_sides_without_moving_the_branch_or_the_tree(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    b = _clone(bare, tmp_path / "b")
    theirs = _commit(b, "from elsewhere", **{"x.md": "1", "y.md": "2"})
    push_origin(b, token=TOKEN)
    ours = _commit(a, "local work", **{"mine.md": "m"})
    (a / "draft.md").write_text("not committed\n")          # untracked
    (a / "README.md").write_text("edited, not committed\n")  # modified
    before = _snapshot(a)

    s = fetch_status(a, token=TOKEN)

    assert _snapshot(a) == before, "a look-only verb changed the branch, the index or the tree"
    assert _run(a, "rev-parse", "refs/remotes/origin/main") == theirs, "only the tracking ref moves"
    assert (s.branch, s.upstream, s.remote) == ("main", "origin/main", "origin")
    assert (s.ahead, s.behind) == (1, 1)
    assert [(c["sha"], c["subject"], c["files"]) for c in s.local_commits] == [(ours, "local work", 1)]
    assert [(c["sha"], c["subject"], c["files"]) for c in s.remote_commits] == [(theirs, "from elsewhere", 2)]
    assert s.uncommitted["modified"] == 1 and s.uncommitted["untracked"] == 1
    assert set(s.uncommitted["paths"]) == {"README.md", "draft.md"}
    assert s.fetched_at and s.fetched_at.endswith("Z")


def test_status_in_sync_reports_zero_and_the_previous_fetch(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    first = fetch_status(a, token=TOKEN)
    second = fetch_status(a, token=TOKEN)
    assert (second.ahead, second.behind) == (0, 0)
    assert second.local_commits == [] and second.remote_commits == []
    assert second.previous_fetch_at is not None and second.previous_fetch_at <= second.fetched_at
    assert first.uncommitted == {"staged": 0, "modified": 0, "untracked": 0, "paths": [], "more": 0}


def test_a_branch_the_remote_does_not_have_is_all_ahead(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    _run(a, "checkout", "-q", "-b", "topic")
    _commit(a, "only here")
    s = fetch_status(a, token=TOKEN)
    assert s.upstream is None and s.branch == "topic"
    assert s.ahead == 2 and s.behind == 0
    assert [c["subject"] for c in s.local_commits] == ["only here", "init"]


def test_no_remote_is_a_typed_refusal(tmp_path):
    ws = tmp_path / "born"; ws.mkdir()
    _run(ws, "init", "-q", "-b", "main"); _run(ws, "config", "user.email", "t@t"); _run(ws, "config", "user.name", "t")
    _commit(ws, "local only")
    with pytest.raises(SyncRefused) as exc:
        fetch_status(ws)
    assert exc.value.kind == "no_remote"


def test_an_unreachable_remote_is_a_typed_network_refusal_and_changes_nothing(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    fetch_status(a, token=TOKEN)
    tracking = _run(a, "rev-parse", "refs/remotes/origin/main")
    before = _snapshot(a)
    _run(a, "remote", "set-url", "origin", "https://github.invalid/acme/kg.git")
    with pytest.raises(SyncRefused) as exc:
        fetch_status(a, token=TOKEN)
    assert exc.value.kind == "network"
    assert TOKEN not in str(exc.value)
    assert exc.value.previous_fetch_at is not None, "the refusal says when the last good look was"
    assert _snapshot(a) == before and _run(a, "rev-parse", "refs/remotes/origin/main") == tracking


# ── the route: who may ask, and that asking changes nothing ─────────────────────────────────────

class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _client(root: Path, index):
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)), membership_index=index))


def _shared_ws(root: Path, workspace_id: str, owner: str, index) -> Path:
    ws = root / workspace_id
    ws.mkdir(parents=True)
    _run(ws, "init", "-q"); _run(ws, "config", "user.email", "t@t"); _run(ws, "config", "user.name", "t")
    (ws / "CLAUDE.md").write_text("# the group's workspace\n")
    _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "seed")
    m.ensure_owner(root, workspace_id, owner, index=index, commit_fn=lambda w, msg: m.policy_commit(w, msg))
    return ws


@pytest.fixture
def attached(tmp_path, monkeypatch):
    idx = m.InMemoryMembershipIndex()
    _shared_ws(tmp_path, "grp-st", "u_owner", idx)
    m.grant_membership(tmp_path, "grp-st", "u_reader", "viewer", index=idx, added_by="u_owner",
                       commit_fn=m.policy_commit)
    bare = gitserve.bare_repo(tmp_path, "statusme")
    repo = gitserve.serve(tmp_path, bare, monkeypatch, repo="statusme")
    c = _client(tmp_path, idx)
    r = c.post("/api/workspace/shared/grp-st/attach", json={"repo": repo}, headers={"X-User-Id": "u_owner"})
    assert r.status_code == 200, r.text
    other = tmp_path / "collab"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
    _run(other, "config", "user.email", "t@t"); _run(other, "config", "user.name", "t")
    (other / "NEW.md").write_text("added elsewhere\n")
    _run(other, "add", "-A"); _run(other, "commit", "-q", "-m", "elsewhere"); _run(other, "push", "-q", "origin", "main")
    return c, tmp_path / "grp-st"


def test_a_member_sees_what_is_behind_and_the_workspace_is_unchanged(attached):
    c, ws = attached
    before = _snapshot(ws)
    r = c.post("/api/workspace/status", json={"slug": "grp-st"}, headers={"X-User-Id": "u_owner"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["behind"] == 1 and body["ahead"] == 0 and body["upstream"] == "origin/main"
    assert [c_["subject"] for c_ in body["remote_commits"]] == ["elsewhere"]
    assert body["fetched_at"]
    assert _snapshot(ws) == before
    assert not (ws / "NEW.md").exists(), "status must never bring the remote's files in"


def test_a_viewer_may_look_because_looking_changes_nothing_they_can_see(attached):
    c, ws = attached
    r = c.post("/api/workspace/status", json={"slug": "grp-st"}, headers={"X-User-Id": "u_reader"})
    assert r.status_code == 200, r.text


def test_a_non_member_is_refused_and_nothing_is_fetched(attached):
    c, ws = attached
    tracking = _run(ws, "rev-parse", "refs/remotes/origin/main")
    r = c.post("/api/workspace/status", json={"slug": "grp-st"}, headers={"X-User-Id": "u_stranger"})
    assert r.status_code in (403, 404), r.text
    assert "elsewhere" not in r.text
    assert _run(ws, "rev-parse", "refs/remotes/origin/main") == tracking


def test_no_remote_is_refused_with_its_kind(tmp_path):
    idx = m.InMemoryMembershipIndex()
    _shared_ws(tmp_path, "grp-none", "u_owner", idx)
    c = _client(tmp_path, idx)
    r = c.post("/api/workspace/status", json={"slug": "grp-none"}, headers={"X-User-Id": "u_owner"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["refused"] == "no_remote"


# ── the declarations: an agent can call it, in the background too ───────────────────────────────

def test_status_is_a_tool_with_a_reentry_row_and_no_person_gate():
    routes = json.loads((AGENT / "routes.v1.json").read_text())
    row = [r for r in routes["routes"] if r["path"] == "/agent/workspace/status"]
    assert row and row[0]["method"] == "POST" and row[0].get("mcp_reentry") is True
    assert ("POST", "/api/workspace/status") not in route_policy.PERSON_VERBS, \
        "a look-only verb is allowed when no person is watching"
    tools = {t["name"]: t for t in json.loads((AGENT / "mcp.tools.v1.json").read_text())["tools"]}
    t = tools["workspace_status"]
    assert t["route"] == {"method": "POST", "path": "/api/workspace/status"}
    assert t["arguments"] == ["slug"]
    worker = json.loads((AGENT / "worker" / "mcp_tools.v1.json").read_text())["tools"]
    assert "workspace_status" in worker


def test_the_tool_description_tells_an_agent_when_to_use_it():
    slice_ = json.loads((AGENT / "mcp.tools.v1.openapi.json").read_text())
    op = slice_["paths"]["/api/workspace/status"]["post"]
    text = op["description"]
    assert text.count(". ") + text.count(".\n") >= 2
    assert "workspace_pull" in text and "workspace_push" in text
