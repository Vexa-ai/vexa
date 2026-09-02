"""deploy_keys — the per-workspace ed25519 key that means nobody pastes a credential into a chat.

The round trip proved here is the real one: generate a key, hand out ONLY the public half, then clone a
repo over git's **ssh transport** using the private half through ``GIT_SSH_COMMAND``. The remote is a
local bare repo and ``ssh`` is a stub script (a fake ssh that drops the options, drops the host and runs
the command locally) — so the transport, the env plumbing and the option list are all exercised for real
without a network or a GitHub account.
"""
import os
import stat
import subprocess
from functools import partial
from pathlib import Path

import pytest

from control_plane import deploy_keys as dk
from control_plane import secret_store as ss
from control_plane.workspace_attach import _git_clone

pytestmark = pytest.mark.skipif(dk.shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not available on this host")


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    monkeypatch.delenv(ss.ENV_KEY_NAME, raising=False)


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _bare_repo_with_a_file(tmp_path: Path) -> Path:
    """A real bare repo with one commit — what the fake ssh will serve."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    (work / "CLAUDE.md").write_text("# governed workspace\n")
    (work / "README.md").write_text("hello from the existing repo\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return bare


def _fake_ssh(tmp_path: Path) -> Path:
    """A stand-in for ``ssh``: log the options we were handed, drop them and the host, run the rest
    locally. This is how the test proves git actually WENT THROUGH GIT_SSH_COMMAND."""
    script = tmp_path / "fake-ssh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{tmp_path}/ssh-args.log"\n'
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -i|-o|-p|-l|-F) shift 2 ;;\n"
        "    -*) shift ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        "shift\n"           # drop the host
        'exec sh -c "$*"\n'
    )
    script.chmod(0o755)
    return script


def test_ensure_is_idempotent_and_returns_only_the_public_half(tmp_path):
    key = dk.workspace_key(workspace_id="acme-deal-a1b2c3")
    first = dk.ensure(tmp_path, key)
    assert first["created"] is True
    assert first["public_key"].startswith("ssh-ed25519 ")
    assert first["fingerprint"].startswith("SHA256:")
    assert "PRIVATE KEY" not in str(first), "the private half must never be in the returned shape"

    second = dk.ensure(tmp_path, key)
    assert second["created"] is False
    assert second["public_key"] == first["public_key"], "a re-ask must not invalidate what they added"


def test_the_private_half_is_sealed_at_rest(tmp_path):
    key = dk.workspace_key(subject="u_jane")
    dk.ensure(tmp_path, key)
    blob = b""
    for dirpath, _d, files in os.walk(tmp_path):
        for f in files:
            blob += (Path(dirpath) / f).read_bytes()
    assert b"OPENSSH PRIVATE KEY" not in blob, "the deploy key's private half must be encrypted at rest"
    assert dk.exists(tmp_path, key)


def test_ssh_env_materializes_a_0600_key_only_for_the_length_of_the_op(tmp_path):
    key = dk.workspace_key(workspace_id="grp-1")
    dk.ensure(tmp_path, key)
    with dk.ssh_env(tmp_path, key) as env:
        cmd = env["GIT_SSH_COMMAND"]
        keyfile = Path(cmd.split(" -i ", 1)[1].split(" ", 1)[0])
        assert keyfile.exists()
        assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
        assert "IdentitiesOnly=yes" in cmd   # the host's own keys can never stand in for this one
        assert "BatchMode=yes" in cmd        # a bad key fails loud instead of hanging on a prompt
    assert not keyfile.exists(), "the private key must not outlive the git op"


def test_no_key_yields_no_env(tmp_path):
    with dk.ssh_env(tmp_path, dk.workspace_key(workspace_id="never-made")) as env:
        assert env is None


def test_clone_over_ssh_with_the_deploy_key(tmp_path, monkeypatch):
    """The end-to-end shape: an ssh:// remote, cloned by the SAME ``_git_clone`` the attach path uses,
    authenticated by the workspace's deploy key through GIT_SSH_COMMAND."""
    bare = _bare_repo_with_a_file(tmp_path)
    fake = _fake_ssh(tmp_path)
    key = dk.workspace_key(workspace_id="grp-ssh")
    dk.ensure(tmp_path / "store", key)

    dest = tmp_path / "clone"
    with dk.ssh_env(tmp_path / "store", key) as env:
        # git composes: <GIT_SSH_COMMAND> <host> git-upload-pack '<path>' — the stub runs the tail.
        env = {**env, "GIT_SSH_COMMAND": env["GIT_SSH_COMMAND"].replace("ssh ", f"{fake} ", 1)}
        clone = partial(_git_clone, ssh_env=env)
        clone(f"ssh://git@fake-host{bare}", "main", dest, None)

    assert (dest / "README.md").read_text().startswith("hello from the existing repo")
    args = (tmp_path / "ssh-args.log").read_text()
    assert "IdentitiesOnly=yes" in args, "the clone must have gone through our GIT_SSH_COMMAND"
    assert "git-upload-pack" in args
    # the token-free origin the attach relies on (nothing embedded, because nothing was)
    origin = subprocess.run(["git", "-C", str(dest), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip()
    assert origin.startswith("ssh://") and "@fake-host" in origin


def test_deploy_keys_url_is_derived_not_guessed():
    assert dk.deploy_keys_url("https://github.com/acme/kg.git") == "https://github.com/acme/kg/settings/keys"
    assert dk.deploy_keys_url("git@github.com:acme/kg.git") == "https://github.com/acme/kg/settings/keys"
    assert dk.deploy_keys_url("https://gitlab.com/acme/kg.git") is None   # we say the state, never a guess


def test_is_ssh_url():
    assert dk.is_ssh_url("git@github.com:acme/kg.git")
    assert dk.is_ssh_url("ssh://git@github.com/acme/kg.git")
    assert not dk.is_ssh_url("https://github.com/acme/kg.git")


def test_an_unsafe_workspace_key_is_refused(tmp_path):
    with pytest.raises(ValueError):
        dk.workspace_key(workspace_id="../escape")
    with pytest.raises(ValueError):
        dk.ensure(tmp_path, "../escape")
