"""The commit SUBJECT names the change — not the agent's reply.

⚠ 2026-09-02, in `_global`'s own history, seen by the founder:

    Done — `STRUCTURE.md` records Vexa as run solo by you, and your desk is
    Here's what's now in `README.md`:

The turn's reply WAS the commit message, cut at 72 characters. A `git log --oneline` of the
company layer read as half-sentences addressed to somebody who is not there, and — the part that
actually costs — you could not see which file a commit touched without opening it. History is what
a person reads when they are trying to find out what happened; it was answering a different
question.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from llm.ports import _change_subject, _commit_env, _commit_mount


def _repo(tmp_path: Path) -> Path:
    ws = tmp_path / "_global"
    ws.mkdir()
    for a in (("init", "-q"), ("config", "user.email", "p@v"), ("config", "user.name", "p")):
        subprocess.run(["git", "-C", str(ws), *a], check=True, capture_output=True)
    (ws / "seed").write_text("x")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "seed"], check=True, capture_output=True)
    return ws


def _subject(ws: Path) -> str:
    return subprocess.run(["git", "-C", str(ws), "log", "-1", "--format=%s"],
                          capture_output=True, text=True).stdout.strip()


def _body(ws: Path) -> str:
    return subprocess.run(["git", "-C", str(ws), "log", "-1", "--format=%b"],
                          capture_output=True, text=True).stdout.strip()


REPLY = "Done — `STRUCTURE.md` records Vexa as run solo by you, and your desk is set up too"


def test_one_file_says_the_workspace_the_path_and_what_happened(tmp_path):
    ws = _repo(tmp_path)
    (ws / "STRUCTURE.md").write_text("# structure\n")
    _commit_mount(ws, message=REPLY, author=("Dmitry", "d@vexa.ai"))
    assert _subject(ws) == "_global: STRUCTURE.md — added"
    # the agent's sentence is not thrown away — it is filed where a sentence belongs
    assert "records Vexa as run solo by you" in _body(ws)


def test_an_edit_reads_as_updated_not_added(tmp_path):
    ws = _repo(tmp_path)
    (ws / "STRUCTURE.md").write_text("one\n")
    _commit_mount(ws, message="x", author=None)
    (ws / "STRUCTURE.md").write_text("two\n")
    _commit_mount(ws, message="y", author=None)
    assert _subject(ws) == "_global: STRUCTURE.md — updated"


def test_several_files_still_say_WHERE_not_only_how_many(tmp_path):
    ws = _repo(tmp_path)
    for n in ("README.md", "PRINCIPLES.md", "OBJECTIVES.md"):
        (ws / n).write_text("x\n")
    _commit_mount(ws, message=REPLY, author=None)
    subj = _subject(ws)
    # "3 files changed" alone would send the reader to the diff for the thing the subject exists
    # to save them, so the first names survive. Git orders the index alphabetically, so the two
    # named are OBJECTIVES.md and PRINCIPLES.md — the point is that NAMES appear, not which.
    assert subj == "_global: OBJECTIVES.md, PRINCIPLES.md +1 — 3 files changed"
    assert "+1" in subj and "3 files changed" in subj


def test_the_subject_never_exceeds_72_characters(tmp_path):
    ws = _repo(tmp_path)
    deep = ws / ("d" * 40)
    deep.mkdir()
    (deep / ("f" * 60 + ".md")).write_text("x\n")
    _commit_mount(ws, message=REPLY, author=None)
    assert len(_subject(ws)) <= 72


def test_a_reply_can_never_become_the_subject_again(tmp_path):
    ws = _repo(tmp_path)
    (ws / "README.md").write_text("x\n")
    _commit_mount(ws, message=REPLY, author=None)
    subj = _subject(ws)
    assert not subj.startswith("Done —")
    assert "your desk is" not in subj
    # and the specific shape of the defect: a truncated sentence promising something it never says
    assert not subj.rstrip().endswith(":")


def test_a_clean_tree_commits_nothing(tmp_path):
    ws = _repo(tmp_path)
    assert _commit_mount(ws, message=REPLY, author=None) is None


def test_the_subject_is_read_off_the_index_not_guessed(tmp_path):
    ws = _repo(tmp_path)
    (ws / "MISSING.md").write_text("x\n")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True, capture_output=True)
    assert _change_subject(ws, _commit_env(None)) == "_global: MISSING.md — added"
