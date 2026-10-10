"""The worker's own git, at write-back, runs nothing the repository names — and the model's tools
cannot write the repository in the first place.

Where the worker runs as root and the model's tools as the tools user, the per-turn grant hands that
user the work tree by group but never ``.git``: a ``.git`` an earlier grant opened is closed again.
And whatever is in ``.git`` — a hook, a hooks path, an fsmonitor, a filter — the write-back commit
(``llm.ports._commit_mount``, through ``llm.gitexec``) does not run it.
"""
from __future__ import annotations

import os
import stat
import subprocess
from collections import namedtuple
from pathlib import Path

import pytest

from llm import ports

PwEntry = namedtuple("PwEntry", "pw_uid pw_gid")
TOOLS_UID_OFFSET = 7


def _raw(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env, check=True)


@pytest.fixture
def ws(tmp_path) -> Path:
    w = tmp_path / "ws"
    (w / "notes").mkdir(parents=True)
    (w / "notes" / "a.md").write_text("one\n")
    _raw(w, "init", "-q")
    _raw(w, "config", "user.email", "t@t")
    _raw(w, "config", "user.name", "t")
    _raw(w, "add", "-A")
    _raw(w, "commit", "-q", "-m", "seed")
    return w


@pytest.fixture
def as_root_with_tools_user(monkeypatch):
    """A root worker whose tools user is NOT this process's user (its gid is ours, so the group
    calls really run)."""
    monkeypatch.setattr(ports.os, "geteuid", lambda: 0)
    tools = PwEntry(os.getuid() + TOOLS_UID_OFFSET, os.getgid())
    monkeypatch.setattr(ports.pwd, "getpwnam", lambda name: tools)
    monkeypatch.setattr(ports, "_tools_off", False)
    return tools


def _modes_under(path: Path):
    yield path, os.lstat(path).st_mode
    for dirpath, dirnames, filenames in os.walk(path):
        for name in (*dirnames, *filenames):
            p = Path(dirpath) / name
            yield p, os.lstat(p).st_mode


def test_the_grant_never_opens_git_and_closes_what_an_earlier_grant_opened(as_root_with_tools_user, ws):
    for p, mode in list(_modes_under(ws / ".git")):           # an earlier grant's leftovers
        if not stat.S_ISLNK(mode):
            os.chmod(p, stat.S_IMODE(mode) | stat.S_IWGRP | stat.S_IWOTH)
    assert ports.grant_tools_access([ws]) is True
    opened = [str(p) for p, mode in _modes_under(ws / ".git")
              if not stat.S_ISLNK(mode) and mode & (stat.S_IWGRP | stat.S_IWOTH)]
    assert opened == []
    # …while the work tree is still the tools user's to write
    assert (ws / "notes" / "a.md").stat().st_mode & stat.S_IWGRP
    assert (ws / "notes").stat().st_mode & stat.S_IWGRP


def test_a_nested_repository_is_kept_closed_too(as_root_with_tools_user, ws):
    inner = ws / "imported"
    inner.mkdir()
    _raw(inner, "init", "-q")
    os.chmod(inner / ".git" / "config", 0o666)
    ports.grant_tools_access([ws])
    assert not (inner / ".git" / "config").stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    assert (inner).stat().st_mode & stat.S_IWGRP


def test_a_work_tree_root_is_sticky_and_nothing_else_is(as_root_with_tools_user, ws):
    """Sticky where a ``.git`` sits, so the tools user cannot rename one it does not own and put
    another directory in its place; ordinary directories keep plain group write."""
    inner = ws / "imported"
    inner.mkdir()
    _raw(inner, "init", "-q")
    assert ports.grant_tools_access([ws]) is True
    for repo_root in (ws, inner):
        mode = repo_root.stat().st_mode
        assert mode & stat.S_ISVTX and mode & stat.S_IWGRP and mode & stat.S_ISGID
    notes = (ws / "notes").stat().st_mode
    assert notes & stat.S_IWGRP and not notes & stat.S_ISVTX


def test_a_git_dir_the_tools_user_owns_is_left_alone(monkeypatch, ws, caplog):
    monkeypatch.setattr(ports.os, "geteuid", lambda: 0)
    os.chmod(ws / ".git" / "config", 0o664)
    ports._keep_git_private(str(ws / ".git"), os.getuid())    # "the tools user" owns it
    assert (ws / ".git" / "config").stat().st_mode & stat.S_IWGRP
    assert "owned by the tools user" in caplog.text


def test_a_hook_planted_in_git_does_not_run_at_write_back(ws, tmp_path):
    marker = tmp_path / "RAN"
    run = f"#!/bin/sh\ntouch '{marker}'\n"
    hooks = ws / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "commit-msg", "post-commit", "reference-transaction"):
        (hooks / name).write_text(run)
        (hooks / name).chmod(0o755)
    elsewhere = tmp_path / "hooks2"
    elsewhere.mkdir()
    (elsewhere / "pre-commit").write_text(run)
    (elsewhere / "pre-commit").chmod(0o755)
    _raw(ws, "config", "core.hooksPath", str(elsewhere))
    _raw(ws, "config", "core.fsmonitor", f"touch '{marker}'; echo")
    _raw(ws, "config", "filter.x.clean", f"sh -c \"touch '{marker}'; cat\"")
    (ws / ".gitattributes").write_text("* filter=x\n")
    (ws / "notes" / "a.md").write_text("two\n")
    sha = ports._commit_mount(ws, message="turn", author=("Ada", "ada@example.com"))
    assert sha and len(sha) == 40
    assert not marker.exists()
    log = subprocess.run(["git", "log", "-1", "--format=%an %s"], cwd=ws, capture_output=True,
                         text=True, check=True).stdout
    assert log.startswith("Ada ")
