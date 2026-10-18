"""shared/gitexec.py — git run here never executes a program the repository names.

Each test plants one class of repository-controlled program in a real repository — a hook, a
``core.hooksPath``, an fsmonitor, a filter, diff and merge drivers, an external diff, an include, a
credential helper, a signing program, a work-tree redirect — and then drives the commands this
domain runs through :func:`run_git`. Every planted program writes a marker file when it runs; no
marker may ever appear. The shape checks (a linked or redirected ``.git``, a foreign owner) are
refused before git starts. And git reads the ``.git`` that was checked: a directory swapped in after
the check — carrying every setting the command line cannot override — is never read.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
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


# ── check, then run: git reads the directory that was checked ─────────────────────────────────

PINNED = sys.platform.startswith("linux")


def _set(path: Path, key: str, value: str) -> None:
    """Write one key into a config FILE the way git quotes it (planting only)."""
    subprocess.run(["git", "config", "--file", str(path), "--add", key, value], check=True,
                   capture_output=True, env={k: v for k, v in os.environ.items()
                                             if not k.startswith("GIT_")})


def _hostile_git_dir(repo: Path, tmp_path: Path, marker: Path) -> Path:
    """A copy of ``repo/.git`` whose configuration names programs only the repository's own file
    can name — filter, diff and merge drivers, reached directly, through an include and through
    ``config.worktree`` — and a work tree whose attributes select them."""
    hostile = tmp_path / "hostile.git"
    shutil.copytree(repo / ".git", hostile, symlinks=True)
    run = f"sh -c \"touch '{marker}'; cat\""
    inc = tmp_path / "inc.cfg"
    for key in ("filter.y.clean", "filter.y.smudge"):
        _set(inc, key, run)
        _set(hostile / "config.worktree", key.replace(".y.", ".z."), run)
    cfg = hostile / "config"
    _set(cfg, "extensions.worktreeConfig", "true")
    subprocess.run(["git", "config", "--file", str(cfg), "core.repositoryformatversion", "1"],
                   check=True)
    _set(cfg, "include.path", str(inc))
    for key in ("filter.x.clean", "filter.x.smudge", "filter.x.process", "diff.x.textconv",
                "diff.x.command", "merge.x.driver"):
        _set(cfg, key, run)
    (repo / ".gitattributes").write_text("* filter=x diff=x merge=x\n*.md filter=y\n*.txt filter=z\n")
    (repo / "b.txt").write_text("b\n")
    return hostile


def _checked(repo: Path) -> Path:
    """Where the swap parks the checked ``.git`` (outside the work tree, so it is not content)."""
    return repo.parent / "checked.git"


def _swap_in(repo: Path, hostile: Path) -> None:
    """What a writer of the work-tree root can do between the check and git's read."""
    (repo / ".git").rename(_checked(repo))
    shutil.copytree(hostile, repo / ".git", symlinks=True)


def _swap_back(repo: Path) -> None:
    shutil.rmtree(repo / ".git")
    _checked(repo).rename(repo / ".git")


def _subjects(repo: Path) -> list:
    return [run_git(repo, "add", "-A"), run_git(repo, "status", "--porcelain"),
            run_git(repo, "commit", "-q", "-m", "change", env=IDENT),
            run_git(repo, "diff", "HEAD~1"), run_git(repo, "log", "-1", "-p"),
            run_git(repo, "checkout", "HEAD", "--", "a.md")]


@pytest.mark.skipif(not PINNED, reason="git is handed the checked directory through /proc (Linux)")
def test_git_keeps_the_pinned_directory_as_it_was_given(repo):
    """The pin rests on git using ``GIT_DIR=/proc/self/fd/N`` as given — reading through the open
    descriptor — rather than resolving it to the directory's name and opening that again. Checked in
    the agent-api and worker images (git 2.47); this keeps the assumption under test wherever the
    suite runs."""
    out = run_git(repo, "rev-parse", "--git-dir")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().startswith("/proc/self/fd/"), out.stdout


@pytest.mark.skipif(not PINNED, reason="git is handed the checked directory through /proc (Linux)")
def test_a_git_dir_swapped_in_after_the_check_is_never_read(repo, marker, tmp_path, monkeypatch):
    hostile = _hostile_git_dir(repo, tmp_path, marker)
    real_run = subprocess.run
    swapped = []

    def run(argv, *a, **kw):              # the window: after every check, right before git starts
        if argv[:2] != ["git", "--no-pager"]:
            return real_run(argv, *a, **kw)
        _swap_in(repo, hostile)
        swapped.append(argv)
        try:
            return real_run(argv, *a, **kw)
        finally:
            _swap_back(repo)

    monkeypatch.setattr(gitexec.subprocess, "run", run)
    (repo / "a.md").write_text("two\n")
    procs = _subjects(repo)
    assert len(swapped) == len(procs)
    assert not marker.exists()
    assert all(p.returncode == 0 for p in procs), [p.stderr for p in procs]
    # git worked in the checked directory: the commit is there, and the swapped-in one is untouched
    log = _raw(repo, "log", "--format=%s").stdout.split()
    assert log == ["change", "seed"]
    assert "change" not in subprocess.run(["git", "--git-dir", str(hostile), "log", "--format=%s"],
                                          capture_output=True, text=True).stdout


def test_a_swap_right_after_the_check_is_never_read(repo, marker, tmp_path, monkeypatch):
    """Pinned (Linux): git still works in the checked directory. Elsewhere: the identity re-check
    refuses before git starts. Either way nothing the swapped-in directory names runs."""
    hostile = _hostile_git_dir(repo, tmp_path, marker)
    real_check = gitexec._check_repository

    def check(root, home):
        result = real_check(root, home)
        if result[0] is not None:
            _swap_in(repo, hostile)
        return result

    monkeypatch.setattr(gitexec, "_check_repository", check)
    (repo / "a.md").write_text("two\n")
    proc = run_git(repo, "add", "-A")
    assert not marker.exists()
    if PINNED:
        assert proc.returncode == 0
        staged = subprocess.run(["git", "--git-dir", str(_checked(repo)), "--work-tree",
                                 str(repo), "diff", "--cached", "--name-only"],
                                capture_output=True, text=True).stdout.split()
        assert "a.md" in staged
    else:
        assert proc.returncode == gitexec.REFUSED and "changed after it was checked" in proc.stderr


def test_without_proc_a_swap_while_git_runs_is_a_refusal(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(gitexec, "_pin", lambda fd: None)     # the path, re-checked by identity
    other = tmp_path / "other.git"
    shutil.copytree(repo / ".git", other, symlinks=True)
    real_run = subprocess.run

    def run(argv, *a, **kw):
        proc = real_run(argv, *a, **kw)
        if argv[:2] == ["git", "--no-pager"]:
            (repo / ".git").rename(repo / ".git-checked")
            other.rename(repo / ".git")
        return proc

    monkeypatch.setattr(gitexec.subprocess, "run", run)
    proc = run_git(repo, "status")
    assert proc.returncode == gitexec.REFUSED and "changed while git ran" in proc.stderr
    (repo / ".git").rename(other)                            # put the checked one back
    (repo / ".git-checked").rename(repo / ".git")
    with pytest.raises(GitRefused):
        run_git(repo, "status", check=True)


def test_a_git_dir_replaced_between_inspection_and_opening_is_refused(repo, tmp_path, monkeypatch):
    real_open = os.open
    other = tmp_path / "other.git"
    shutil.copytree(repo / ".git", other, symlinks=True)

    def opener(path, flags, *a, **kw):
        if os.fspath(path) == os.path.join(str(repo), ".git"):
            (repo / ".git").rename(repo / ".git-checked")
            other.rename(repo / ".git")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(gitexec.os, "open", opener)
    proc = run_git(repo, "status")
    assert proc.returncode == gitexec.REFUSED and "changed while it was being checked" in proc.stderr


def test_a_git_dir_that_appears_after_the_check_is_not_discovered(tmp_path, marker, monkeypatch):
    ws = tmp_path / "plain"
    ws.mkdir()
    source = tmp_path / "src"
    source.mkdir()
    _raw(source, "init", "-q")
    _raw(source, "config", "core.fsmonitor", f"touch '{marker}'; echo")
    _raw(source, "config", "filter.x.clean", f"sh -c \"touch '{marker}'; cat\"")
    (ws / ".gitattributes").write_text("* filter=x\n")
    real_check = gitexec._check_repository

    def check(root, home):
        result = real_check(root, home)
        if os.path.realpath(root) == os.path.realpath(ws):
            shutil.copytree(source / ".git", ws / ".git", symlinks=True)
        return result

    monkeypatch.setattr(gitexec, "_check_repository", check)
    proc = run_git(ws, "add", "-A")
    assert proc.returncode != 0 and "not a git repository" in proc.stderr
    assert not marker.exists()


def test_gits_own_ownership_rule_still_applies(repo, monkeypatch):
    """GIT_DIR is explicit, which skips git's ``safe.directory`` check — so it is made here: a
    ``.git`` and work tree owned by someone else are refused unless the system lists the work tree."""
    real_lstat = os.lstat
    targets = {os.path.join(str(repo), ".git"), str(repo)}

    def lstat(path, *a, **kw):
        st = real_lstat(path, *a, **kw)
        if os.fspath(path) in targets:
            fields = list(st)
            fields[stat.ST_UID] = st.st_uid + 4242
            return os.stat_result(fields)
        return st

    monkeypatch.setattr(gitexec.os, "lstat", lstat)
    monkeypatch.setattr(gitexec, "_safe_directories", [])
    proc = run_git(repo, "status")
    assert proc.returncode == gitexec.REFUSED and "dubious ownership" in proc.stderr
    monkeypatch.setattr(gitexec, "_safe_directories", [str(repo)])
    assert run_git(repo, "status").returncode == 0
    monkeypatch.setattr(gitexec, "_safe_directories", [str(repo.parent) + "/*"])
    assert run_git(repo, "status").returncode == 0
    monkeypatch.setattr(gitexec, "_safe_directories", ["*"])
    assert run_git(repo, "status").returncode == 0
