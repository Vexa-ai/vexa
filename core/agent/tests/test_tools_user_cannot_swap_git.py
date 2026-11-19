"""As root, with the real tools user: the per-turn grant leaves the model's tools unable to swap a
work tree's ``.git``, and able to do everything a turn normally does.

The swap is the check-then-run window in ``shared/gitexec.py``: a writer of the work-tree root that
could rename ``.git`` and put another directory in its place between git's check and git's read.
The grant makes every directory holding a ``.git`` sticky, so that rename is refused by the kernel.
This needs a root worker and the image's tools user (``core/agent/worker/Dockerfile``); elsewhere it
skips. Run it in the worker image:
``docker run --rm -v <repo>/core/agent/tests:/app/tests <worker image> python -m pytest tests/test_tools_user_cannot_swap_git.py``.
"""
from __future__ import annotations

import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from llm import ports
from llm.gitexec import run_git


def _tools_user_present() -> bool:
    try:
        pwd.getpwnam(ports.TOOLS_USER)
    except KeyError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() != 0 or not _tools_user_present(),
    reason="needs a root worker and the image's tools user")

#: Runs as the tools user. Each step reports whether the kernel allowed it.
_AS_TOOLS = r'''
import json, os, sys
ws = sys.argv[1]
j = lambda *p: os.path.join(ws, *p)
out = {}
def attempt(name, fn):
    try:
        fn()
        out[name] = "ok"
    except OSError as exc:
        out[name] = exc.__class__.__name__
def write(path, text, mode="w"):
    with open(path, mode) as fh:
        fh.write(text)
attempt("rename_git_away", lambda: os.rename(j(".git"), j(".git-away")))
attempt("rename_root_owned_dir_over_git", lambda: os.rename(j("notes"), j(".git")))
attempt("rename_root_owned_dir_at_root", lambda: os.rename(j("notes"), j("notes-away")))
attempt("write_inside_git", lambda: write(j(".git", "config"), "[core]\n\tx = 1\n", "a"))
attempt("create_in_git", lambda: write(j(".git", "planted"), "x"))
attempt("create_own_file", lambda: write(j("mine.md"), "one\n"))
attempt("edit_own_file", lambda: write(j("mine.md"), "two\n"))
attempt("rename_own_file", lambda: os.rename(j("mine.md"), j("mine2.md")))
attempt("replace_own_file_atomically", lambda: (write(j("mine2.md.tmp"), "three\n"), os.replace(j("mine2.md.tmp"), j("mine2.md"))))
attempt("delete_own_file", lambda: os.unlink(j("mine2.md")))
attempt("make_own_dir", lambda: os.mkdir(j("drafts")))
attempt("edit_root_owned_file_in_place", lambda: write(j("README.md"), "edited by the model\n"))
attempt("edit_root_owned_file_in_subdir", lambda: write(j("notes", "a.md"), "edited\n"))
attempt("rename_root_owned_file_in_subdir", lambda: os.rename(j("notes", "a.md"), j("notes", "b.md")))
attempt("delete_root_owned_file_in_subdir", lambda: os.unlink(j("notes", "b.md")))
attempt("rename_root_owned_file_at_root", lambda: os.rename(j("README.md"), j("README.old")))
print(json.dumps(out))
'''


@pytest.fixture
def ws():
    base = Path(tempfile.mkdtemp(prefix="vexa-sticky-"))
    os.chmod(base, 0o755)                                 # the tools user must reach the work tree
    w = base / "ws"
    (w / "notes").mkdir(parents=True)
    (w / "notes" / "a.md").write_text("one\n")
    (w / "README.md").write_text("# ws\n")
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t"),
                 ("add", "-A"), ("commit", "-q", "-m", "seed")):
        run_git(w, *args, check=True)
    yield w
    shutil.rmtree(base, ignore_errors=True)


def test_the_tools_user_cannot_swap_git_and_can_still_do_its_work(ws):
    assert ports.grant_tools_access([ws]) is True
    assert ws.stat().st_mode & stat.S_ISVTX
    gitdir = ws / ".git"
    assert gitdir.stat().st_uid == os.geteuid()
    assert not gitdir.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)

    proc = subprocess.run([sys.executable, "-c", _AS_TOOLS, str(ws)], capture_output=True,
                          text=True, check=True, **ports.harness_identity_kwargs())
    got = json.loads(proc.stdout)

    # the swap, and anything that would do it, is refused
    assert got["rename_git_away"] == "PermissionError"
    assert got["rename_root_owned_dir_over_git"] != "ok"
    assert got["rename_root_owned_dir_at_root"] == "PermissionError"
    assert got["write_inside_git"] == "PermissionError" and got["create_in_git"] == "PermissionError"
    # a turn's ordinary work is unchanged
    for step in ("create_own_file", "edit_own_file", "rename_own_file", "replace_own_file_atomically",
                 "delete_own_file", "make_own_dir", "edit_root_owned_file_in_place",
                 "edit_root_owned_file_in_subdir", "rename_root_owned_file_in_subdir",
                 "delete_root_owned_file_in_subdir"):
        assert got[step] == "ok", (step, got)
    # the one thing the sticky root costs: an entry at the root the tools user does not own
    # cannot be renamed or removed by it (editing it in place still works, above)
    assert got["rename_root_owned_file_at_root"] == "PermissionError"

    assert gitdir.is_dir() and not (ws / ".git-away").exists()
    sha = ports._commit_mount(ws, message="turn", author=("Ada", "ada@example.com"))
    assert sha and len(sha) == 40
    assert "README.md" in run_git(ws, "show", "--name-only", "--format=", "HEAD", check=True).stdout
