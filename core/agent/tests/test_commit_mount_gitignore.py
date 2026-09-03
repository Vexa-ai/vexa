"""The post-turn commit must land even when the workspace `.gitignore` already ignores `.claude`.

The break this guards (found 2026-09-02 replaying DNA fixtures): ``_commit_mount`` staged with
``git add -A -- . ':(exclude).claude'``. An exclude pathspec still counts as *explicitly naming*
the path, so whenever a `.gitignore` also matches `.claude` git exits 1 with "The following paths
are ignored by one of your .gitignore files". ``_git`` runs ``check=True``, and
``run_harness_turn``'s ``except CalledProcessError: continue`` swallowed it — so on EVERY seeded
workspace (they all ship that `.gitignore`) every chat turn left its work STAGED AND NEVER
COMMITTED, with no error anywhere. Downstream, the flows engine detects a finished meeting note by
a COMMIT, so post-meeting minutes could never complete.

Both halves are asserted, because either alone regrows the bug: the commit must happen, and
`.claude` must stay out of it — with and without a `.gitignore`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from llm.ports import _commit_mount


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-c", "user.email=t@test", "-c", "user.name=t", *args],
                          cwd=str(cwd), check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def _workspace(tmp_path: Path, *, gitignore: bool) -> Path:
    ws = tmp_path / ("ignored" if gitignore else "legacy")
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "session.json").write_text('{"id": "private"}\n')
    (ws / "README.md").write_text("# seed\n")
    if gitignore:
        (ws / ".gitignore").write_text(".claude/\n")
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "add", "-A", "--", ".")
    _git(ws, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", ".claude")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _tracked(ws: Path) -> list[str]:
    return _git(ws, "ls-tree", "-r", "--name-only", "HEAD").splitlines()


def test_commit_lands_with_gitignored_claude(tmp_path):
    """The regression: a seeded workspace (`.gitignore` present) must still commit."""
    ws = _workspace(tmp_path, gitignore=True)
    head_before = _git(ws, "rev-parse", "HEAD")

    (ws / "kg").mkdir()
    (ws / "kg" / "note.md").write_text("# the turn's work\n")
    (ws / ".claude" / "session.json").write_text('{"id": "changed mid-turn"}\n')

    sha = _commit_mount(ws, message="agent turn", author=("68", "68@vexa.local"))

    assert sha, "the turn's work was staged but never committed"
    assert sha != head_before
    tracked = _tracked(ws)
    assert "kg/note.md" in tracked
    assert not any(p.startswith(".claude") for p in tracked)


def test_commit_excludes_claude_without_a_gitignore(tmp_path):
    """The case the exclusion exists for: a legacy mount with no `.gitignore` must not sweep
    `.claude` into history. Dropping the pathspec entirely would pass the test above and fail here."""
    ws = _workspace(tmp_path, gitignore=False)

    (ws / "kg").mkdir()
    (ws / "kg" / "note.md").write_text("# the turn's work\n")
    (ws / ".claude" / "leaked.json").write_text('{"transcript": "private"}\n')

    sha = _commit_mount(ws, message="agent turn", author=("68", "68@vexa.local"))

    assert sha
    tracked = _tracked(ws)
    assert "kg/note.md" in tracked
    assert not any(p.startswith(".claude") for p in tracked), \
        "harness continuity must never enter workspace git history"


def test_clean_tree_is_a_no_op(tmp_path):
    ws = _workspace(tmp_path, gitignore=True)
    assert _commit_mount(ws, message="agent turn", author=None) is None
