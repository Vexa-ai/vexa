"""shared/gitexec.py — git run here never executes a program the repository names.

Each test plants one class of repository-controlled program in a real repository — a hook, a
``core.hooksPath``, an fsmonitor, a filter, diff and merge drivers, an external diff, an include, a
credential helper, a signing program, a work-tree redirect — and then drives the commands this
domain runs through :func:`run_git`. Every planted program writes a marker file when it runs; no
marker may ever appear. The shape checks (a linked or redirected ``.git``, a foreign owner) are
refused before git starts.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from shared import gitexec
from shared.gitexec import GitRefused, run_git

IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _raw(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Plain git, for PLANTING only (it is what an untrusted writer of the repository would run)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(IDENT)
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env, check=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    _raw(ws, "init", "-q")
    _raw(ws, "config", "user.email", "t@t")
    _raw(ws, "config", "user.name", "t")
    (ws / "a.md").write_text("one\n")
    _raw(ws, "add", "-A")
    _raw(ws, "commit", "-q", "-m", "seed")
    return ws


@pytest.fixture
def marker(tmp_path) -> Path:
    return tmp_path / "RAN"


def _cmd(marker: Path) -> str:
    return f"touch '{marker}'"


def _change_and_commit(repo: Path) -> None:
    (repo / "a.md").write_text("two\n")
    run_git(repo, "add", "-A", check=True)
    run_git(repo, "status", "--porcelain", check=True)
    run_git(repo, "commit", "-q", "-m", "change", env=IDENT, check=True)
    run_git(repo, "diff", "HEAD~1", check=True)
    run_git(repo, "show", "--format=", "HEAD", check=True)
    run_git(repo, "log", "-1", "-p", check=True)


@pytest.mark.parametrize("hook", ["pre-commit", "prepare-commit-msg", "commit-msg", "post-commit",
                                  "reference-transaction", "post-index-change"])
def test_a_hook_planted_in_the_repository_does_not_run(repo, marker, hook):
    h = repo / ".git" / "hooks" / hook
    h.parent.mkdir(exist_ok=True)
    h.write_text(f"#!/bin/sh\n{_cmd(marker)}\n")
    h.chmod(0o755)
    _change_and_commit(repo)
    assert not marker.exists()


def test_a_hooks_path_the_repository_names_is_not_used(repo, marker, tmp_path):
    hooks = tmp_path / "elsewhere"
    hooks.mkdir()
    (hooks / "pre-commit").write_text(f"#!/bin/sh\n{_cmd(marker)}\n")
    (hooks / "pre-commit").chmod(0o755)
    _raw(repo, "config", "core.hooksPath", str(hooks))
    _change_and_commit(repo)
    assert not marker.exists()
    assert "hookspath" not in (repo / ".git" / "config").read_text().lower()


def test_an_fsmonitor_the_repository_names_does_not_run(repo, marker):
    _raw(repo, "config", "core.fsmonitor", f"{_cmd(marker)}; echo")
    _change_and_commit(repo)
    assert not marker.exists()


@pytest.mark.parametrize("key", ["clean", "smudge", "process"])
def test_a_filter_driver_does_not_run(repo, marker, key):
    _raw(repo, "config", f"filter.x.{key}", f"sh -c \"{_cmd(marker)}; cat\"")
    (repo / ".gitattributes").write_text("* filter=x\n")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* filter=x\n")
    _change_and_commit(repo)
    run_git(repo, "checkout", "HEAD~1", "--", "a.md", check=True)
    assert not marker.exists()
    assert "filter" not in (repo / ".git" / "config").read_text()


def test_diff_drivers_and_an_external_diff_do_not_run(repo, marker):
    _raw(repo, "config", "diff.x.textconv", f"sh -c \"{_cmd(marker)}; cat \\\"$1\\\"\" -")
    _raw(repo, "config", "diff.x.command", _cmd(marker))
    _raw(repo, "config", "diff.external", _cmd(marker))
    _raw(repo, "config", "merge.x.driver", _cmd(marker))
    (repo / ".gitattributes").write_text("* diff=x merge=x\n")
    _change_and_commit(repo)
    assert not marker.exists()


def test_an_include_is_removed_before_git_reads_it(repo, marker, tmp_path):
    inc = tmp_path / "inc.cfg"
    inc.write_text(f"[core]\n\tfsmonitor = {_cmd(marker)}; echo\n")
    _raw(repo, "config", "include.path", str(inc))
    _raw(repo, "config", f"includeIf.gitdir:{repo}/.path", str(inc))
    _change_and_commit(repo)
    assert not marker.exists()
    assert "include" not in (repo / ".git" / "config").read_text().lower()


def test_a_credential_helper_the_repository_names_does_not_run(repo, marker):
    _raw(repo, "config", "credential.helper", f"!{_cmd(marker)}; :")
    _raw(repo, "config", "credential.https://example.com.helper", f"!{_cmd(marker)}; :")
    proc = run_git(repo, "credential", "fill", input="protocol=https\nhost=example.com\n\n")
    assert proc.returncode != 0            # nothing answers, and no prompt is possible
    assert not marker.exists()


def test_signing_programs_the_repository_names_do_not_run(repo, marker):
    _raw(repo, "config", "commit.gpgSign", "true")
    _raw(repo, "config", "gpg.program", _cmd(marker))
    _raw(repo, "config", "gpg.format", "ssh")
    _raw(repo, "config", "gpg.ssh.program", _cmd(marker))
    _change_and_commit(repo)
    run_git(repo, "log", "-1", "--show-signature", check=True)
    assert not marker.exists()


def test_a_redirected_work_tree_is_ignored(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "secret.txt").write_text("not this workspace's\n")
    _raw(repo, "config", "core.worktree", str(other))
    run_git(repo, "add", "-A", check=True)
    staged = run_git(repo, "diff", "--cached", "--name-only", check=True).stdout
    assert "secret.txt" not in staged
    assert "worktree" not in (repo / ".git" / "config").read_text()


def test_kept_settings_survive_the_reduction(repo):
    _raw(repo, "remote", "add", "origin", "https://example.com/o/r.git")
    _raw(repo, "config", "branch.main.remote", "origin")
    _raw(repo, "config", "core.pager", "less")          # one removable key forces a rewrite
    run_git(repo, "status", check=True)
    pairs = dict(gitexec._list_config(str(repo / ".git" / "config"), gitexec.private_dir()))
    assert pairs["remote.origin.url"] == "https://example.com/o/r.git"
    assert pairs["remote.origin.fetch"] == "+refs/heads/*:refs/remotes/origin/*"
    assert pairs["branch.main.remote"] == "origin"
    assert pairs["user.email"] == "t@t" and "core.pager" not in pairs


# ── the repository's shape ─────────────────────────────────────────────────────────────────────

def test_a_linked_git_dir_is_refused(repo, tmp_path):
    real = tmp_path / "real.git"
    (repo / ".git").rename(real)
    (repo / ".git").symlink_to(real, target_is_directory=True)
    proc = run_git(repo, "status")
    assert proc.returncode == gitexec.REFUSED and "symbolic link" in proc.stderr
    with pytest.raises(GitRefused):
        run_git(repo, "status", check=True)


def test_a_gitdir_file_is_refused(repo, tmp_path):
    (repo / ".git").rename(tmp_path / "moved.git")
    (repo / ".git").write_text(f"gitdir: {tmp_path / 'moved.git'}\n")
    assert run_git(repo, "status").returncode == gitexec.REFUSED


@pytest.mark.parametrize("redirect", ["commondir", "objects/info/alternates"])
def test_a_repository_redirect_is_refused(repo, tmp_path, redirect):
    p = repo / ".git" / redirect
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(tmp_path) + "\n")
    assert run_git(repo, "status").returncode == gitexec.REFUSED


def test_a_linked_config_is_refused(repo, tmp_path):
    cfg = repo / ".git" / "config"
    real = tmp_path / "cfg"
    cfg.rename(real)
    cfg.symlink_to(real)
    assert run_git(repo, "status").returncode == gitexec.REFUSED


def test_a_git_dir_owned_by_someone_else_is_refused(repo, monkeypatch):
    real_lstat = os.lstat
    gitdir = os.path.join(str(repo), ".git")

    def lstat(path, *a, **kw):
        st = real_lstat(path, *a, **kw)
        if os.fspath(path) == gitdir:
            fields = list(st)
            fields[stat.ST_UID] = st.st_uid + 4242
            return os.stat_result(fields)
        return st

    monkeypatch.setattr(gitexec.os, "lstat", lstat)
    proc = run_git(repo, "status")
    assert proc.returncode == gitexec.REFUSED and "owned" in proc.stderr


def test_discovery_never_walks_up_into_a_parent_repository(repo):
    sub = repo / "plain"
    sub.mkdir()
    proc = run_git(sub, "rev-parse", "--git-dir")
    assert proc.returncode != 0


# ── the environment ────────────────────────────────────────────────────────────────────────────

def test_inherited_redirects_are_dropped(repo, tmp_path, marker, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    _raw(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.fsmonitor'='{_cmd(marker)}; echo'")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", _cmd(marker))
    top = run_git(repo, "rev-parse", "--show-toplevel", check=True).stdout.strip()
    assert os.path.realpath(top) == os.path.realpath(repo)
    _change_and_commit(repo)
    assert not marker.exists()


@pytest.mark.parametrize("var", ["HOME", "GIT_DIR", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_COUNT",
                                 "GIT_WORK_TREE", "GIT_EXTERNAL_DIFF"])
def test_a_caller_cannot_set_what_gitexec_owns(repo, var):
    with pytest.raises(ValueError):
        run_git(repo, "status", env={var: "x"})


@pytest.mark.parametrize("args", [("-C", "/", "status"), ("--git-dir=/x", "status"),
                                  ("-c", "core.hooksPath=/x", "status"), ("--work-tree", "/", "status")])
def test_a_caller_cannot_redirect_git_or_reopen_a_setting(repo, args):
    with pytest.raises(ValueError):
        run_git(repo, *args)


def test_no_transport_unless_the_caller_names_one(repo, tmp_path):
    dest = tmp_path / "clone"
    refused = run_git(tmp_path, "clone", "-q", "--", str(repo), str(dest))
    assert refused.returncode != 0 and not dest.exists()
    run_git(tmp_path, "clone", "-q", "--", str(repo), str(dest), env={"GIT_ALLOW_PROTOCOL": "file"},
            check=True)
    assert (dest / "a.md").read_text() == "one\n"


def test_the_private_directory_is_ours_and_holds_no_hooks():
    home = Path(gitexec.private_dir())
    st = home.stat()
    assert st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == 0o700
    assert list((home / "hooks").iterdir()) == []
    assert "bareRepository = explicit" in (home / "gitconfig").read_text()
