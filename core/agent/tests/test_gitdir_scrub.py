"""gitdir scrub — the workspace git helpers must NEVER operate on a repo exported via GIT_DIR.

Reproduces (safely) the pre-push data-loss incident: git exports GIT_DIR into hook descendants, so
`pnpm gates` → pytest ran every workspace `git add -A && git commit` against the REAL repo, with the
test's tmp cwd as its work tree — rewriting the branch being pushed with junk commits that deleted
the tree. Here a scratch "victim" repo plays the real repo: we set GIT_DIR at it, run the workspace
git helpers in a separate tmp dir, and assert the victim is untouched while the helper operated on
its own directory. Guards the scrub in shared/gitenv.py (and its llm/ports.py twin).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from shared.gitenv import GIT_REPO_DISCOVERY_VARS, scrubbed_git_env
from shared.seeding import seed_workspace


def _clean_git(cwd: Path, *args: str) -> str:
    """The test's OWN git runner — explicitly scrubbed so victim setup/verification can never be
    re-pointed by the very vars this test sets."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_REPO_DISCOVERY_VARS}
    proc = subprocess.run(
        ["git", "-c", "user.email=victim@test", "-c", "user.name=victim", *args],
        cwd=str(cwd), env=env, check=True, capture_output=True, text=True,
    )
    return proc.stdout.strip()


def _make_victim(tmp_path: Path) -> tuple[Path, str, str]:
    """A scratch repo with one commit — the stand-in for the real repo a hook would export."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.txt").write_text("do not lose me\n")
    _clean_git(victim, "init", "-q", "-b", "main")
    _clean_git(victim, "add", "-A")
    _clean_git(victim, "commit", "-q", "-m", "precious")
    return victim, _clean_git(victim, "rev-parse", "HEAD"), _clean_git(victim, "symbolic-ref", "HEAD")


def test_seed_workspace_ignores_hook_exported_git_dir(tmp_path, monkeypatch):
    victim, head_before, branch_before = _make_victim(tmp_path)

    # The hook environment: GIT_DIR points at the victim (as git's pre-push export does). Without
    # the scrub, seed_workspace's init/add/commit would treat its tmp cwd as the VICTIM's work
    # tree and rewrite the victim's branch with a junk commit deleting precious.txt.
    monkeypatch.setenv("GIT_DIR", str(victim / ".git"))

    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("governance root\n")
    ws = tmp_path / "ws" / "u1"
    seed_workspace(ws, seed)

    # The victim is untouched: same HEAD, same branch, tree intact, no new commits.
    assert _clean_git(victim, "rev-parse", "HEAD") == head_before
    assert _clean_git(victim, "symbolic-ref", "HEAD") == branch_before
    assert (victim / "precious.txt").read_text() == "do not lose me\n"
    assert _clean_git(victim, "log", "--oneline").count("\n") == 0  # still exactly one commit
    assert _clean_git(victim, "status", "--porcelain") == ""

    # And the helper operated on ITS OWN directory: ws is a real repo with the seed commit.
    assert (ws / ".git").exists()
    assert _clean_git(ws, "log", "--format=%s") == "seed"


def test_run_harness_turn_commit_ignores_hook_exported_git_dir(tmp_path, monkeypatch):
    """The other write path: llm.run_harness_turn's post-turn commit (llm/ports.py's local _git)."""
    from llm import run_harness_turn

    victim, head_before, _ = _make_victim(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(victim / ".git"))

    ws = tmp_path / "work"
    ws.mkdir()
    _clean_git(ws, "init", "-q")
    _clean_git(ws, "config", "user.email", "agent@vexa")
    _clean_git(ws, "config", "user.name", "vexa-agent")

    class _FakeHarness:
        name = "fake"

        def prepare(self, work):  # noqa: ANN001 - HarnessPort shape
            pass

        def run_turn(self, work, prompt, **kwargs):
            (Path(work) / "note.md").write_text("a governed write\n")
            yield {"type": "done", "reply": "ok", "sessionId": "s1", "ok": True}

    events = list(run_harness_turn(ws, "write a note", _FakeHarness()))

    commit = [e for e in events if e.get("type") == "commit"]
    assert commit and commit[0]["sha"], "the turn must commit its own workspace"
    assert _clean_git(victim, "rev-parse", "HEAD") == head_before  # victim untouched
    assert _clean_git(ws, "rev-parse", "HEAD") == commit[0]["sha"]  # commit landed on ws


def test_scrubbed_git_env_drops_discovery_vars_only(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "kept")  # identity injection is deliberate — keep it
    env = scrubbed_git_env(GIT_ASKPASS="true")
    assert all(v not in env for v in GIT_REPO_DISCOVERY_VARS)
    assert env["GIT_AUTHOR_NAME"] == "kept"
    assert env["GIT_ASKPASS"] == "true"  # overrides land

    from llm.ports import scrubbed_git_env as llm_scrub  # the module-local twin stays in lockstep
    assert all(v not in llm_scrub() for v in GIT_REPO_DISCOVERY_VARS)
