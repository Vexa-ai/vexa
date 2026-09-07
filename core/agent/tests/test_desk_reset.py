"""PUTTING A DESK BACK ON A WITNESSED SHA — `POST /api/workspace/git/reset` (Vexa-ai/vexa#1606).

The decision-22 detector in `process_meeting` was loud, correct, and un-actionable by the system
that raised it: on 2026-09-06 the recovery both times was a person resetting a repository by hand
and re-firing the reaction, while a grounded report sat unsent. This route is the half of that
recovery a flow step can perform — and because "reset a repository" is a large-sounding capability,
what it CANNOT do is most of what this file asserts.

  * internal tier only — a signed-in browser client cannot reach it at all;
  * the caller's OWN primary desk, with no `slug` on the wire: it can never be aimed at a shared
    workspace or at somebody else's;
  * BACKWARD only, along THIS history — the sha must be an ancestor of the current HEAD, so it can
    only remove commits made after the witness. It can never fast-forward a desk onto work it has
    not done, and cannot be aimed at an unrelated history;
  * a refusal is a 200 with `reset: false` and a reason, because its one caller is already inside a
    failure and needs to REPORT why the repair did not happen, not to die a second time.

Backend-free: real git repos in tmp dirs, no docker.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

INTERNAL_SECRET = "s3cr3t-internal"
INTERNAL = {"X-User-Id": "58", "X-Internal-Secret": INTERNAL_SECRET}
BROWSER = {"X-User-Id": "58"}


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _git(d: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True).stdout.strip()


def _repo(d: Path, marker: str = "X") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for a in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
              ("config", "user.name", "t")):
        _git(d, *a)
    (d / "CLAUDE.md").write_text(marker)
    _git(d, "add", "-A"); _git(d, "commit", "-q", "-m", "seed")
    return d


def _commit(d: Path, name: str, body: str) -> str:
    (d / name).write_text(body)
    _git(d, "add", "-A"); _git(d, "commit", "-q", "-m", f"{d.name}: {name} — updated")
    return _git(d, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "ws"
    desk = _repo(root / "58", "SEED 58")
    _repo(root / "u_bob", "SEED bob")
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(_repo(tmp_path / "g", "G")),
                             internal_api_secret=INTERNAL_SECRET)
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     reader=WorkspaceReader(str(root)))
    return TestClient(app), root, desk


def test_it_removes_the_commits_a_room_run_should_never_have_made(world):
    """The 2026-09-06 shape exactly: three `README.md — updated` commits on top of the witness."""
    c, _root, desk = world
    witness = _git(desk, "rev-parse", "HEAD")
    for i in range(3):
        _commit(desk, "README.md", f"regenerated section {i}")
    assert _git(desk, "rev-parse", "HEAD") != witness

    r = c.post("/api/workspace/git/reset", json={"sha": witness, "reason": "decision 22"},
               headers=INTERNAL)
    assert r.status_code == 200
    assert r.json()["reset"] is True and r.json()["after"] == witness
    assert _git(desk, "rev-parse", "HEAD") == witness
    # HARD, on purpose: a soft reset would leave the stray content staged for the next writer to
    # commit under a different message, which is the same defect wearing a different subject line.
    assert _git(desk, "status", "--porcelain") == ""


def test_a_browser_client_cannot_reach_it_at_all(world):
    """The internal-tier gate is the whole trust boundary — the same edge the meeting room opens
    on. No session cookie can be replayed into discarding a person's own history."""
    c, _root, desk = world
    head = _commit(desk, "note.md", "a real write by its owner")
    assert c.post("/api/workspace/git/reset", json={"sha": "HEAD"},
                  headers=BROWSER).status_code == 403
    assert _git(desk, "rev-parse", "HEAD") == head


def test_it_only_ever_goes_BACKWARD_along_this_history(world):
    """The property that keeps this small. A sha that is not an ancestor of HEAD is refused, so the
    route can only undo commits made after the witness — never move a desk onto history it has not
    done, and never onto an unrelated one."""
    c, root, desk = world
    head = _git(desk, "rev-parse", "HEAD")
    stranger = _git(root / "u_bob", "rev-parse", "HEAD")

    for sha, why in ((stranger, "another workspace's history"),
                     ("0" * 40, "a commit that does not exist"),
                     ("nope", "not a commit id at all")):
        r = c.post("/api/workspace/git/reset", json={"sha": sha}, headers=INTERNAL)
        assert r.status_code == 200, why
        assert r.json()["reset"] is False, why
        assert r.json()["detail"], f"a refusal with no reason is unactionable ({why})"
    assert _git(desk, "rev-parse", "HEAD") == head


def test_it_takes_no_workspace_name(world):
    """A `slug` would make this a way to rewrite a shared desk, or somebody else's, from the
    internal tier. The route reads the caller's own primary and ignores anything else on the body."""
    c, root, desk = world
    bob = root / "u_bob"
    bob_head = _git(bob, "rev-parse", "HEAD")
    _commit(bob, "README.md", "bob's own work")
    witness = _git(desk, "rev-parse", "HEAD")
    _commit(desk, "README.md", "stray")

    r = c.post("/api/workspace/git/reset", json={"sha": witness, "slug": "u_bob"}, headers=INTERNAL)
    assert r.json()["reset"] is True
    assert _git(desk, "rev-parse", "HEAD") == witness       # the caller's own desk moved
    assert _git(bob, "rev-parse", "HEAD") != bob_head       # ...and bob's did not
    assert (bob / "README.md").read_text() == "bob's own work"


def test_a_sha_is_required_and_an_unmoved_desk_is_a_no_op(world):
    c, _root, desk = world
    assert c.post("/api/workspace/git/reset", json={}, headers=INTERNAL).status_code == 400
    head = _git(desk, "rev-parse", "HEAD")
    out = c.post("/api/workspace/git/reset", json={"sha": head}, headers=INTERNAL).json()
    assert out["reset"] is False and out["detail"] == "HEAD is already there"
    assert _git(desk, "rev-parse", "HEAD") == head
