"""What a caller-supplied "Repository" may be — the gate that runs BEFORE git does.

``POST /api/workspace/swap`` and ``POST /api/workspace/activate`` hand the ``repo`` field to
``git clone``, which is a fetch THIS SERVER performs. Two things follow, and each has its own half of
this file:

* the HOST must be one the caller could have reached themselves — otherwise the attach dialog is a
  server-side request forge against the deployment's own network (R-D15);
* the TRANSPORT must be one we named — ``ext::`` runs a shell command and ``file://`` reads this
  host's disk, and both are well-formed git URLs.

The load-bearing assertion in the first half is not the error: it is that **no subprocess ever ran**.
A validator that fires after git has already been told where to go has not protected anything, so
every refusal test monkeypatches ``subprocess.run``/``Popen`` and asserts the call count is zero.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import repo_ref
from control_plane import workspace_attach
from control_plane.repo_ref import (
    RepoRefError,
    assert_allowed_scheme,
    assert_fetchable,
    assert_public_host,
    _host_is_internal,
)
from shared.gitenv import git_transports_for, pinned_git_env


@pytest.fixture
def no_subprocess(monkeypatch):
    """Every way a git process could be started, replaced by a counter. The refusal tests assert this
    stays at zero — the whole point of the gate is that it runs first."""
    calls: list[tuple] = []

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError(f"a subprocess was started despite the gate: {args!r}")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    return calls


# ── the host half: a fetch the caller could not have made themselves ──────────────────────────────

INTERNAL = [
    "http://127.0.0.1/a/b",                  # loopback
    "https://127.0.0.1:8080/a/b",            # …with a port
    "http://10.0.0.5/a/b",                   # RFC1918
    "http://192.168.1.10/a/b",
    "http://172.16.0.1/a/b",
    "http://169.254.169.254/latest/meta-data",   # the cloud metadata service
    "http://[::1]/a/b",                      # loopback, IPv6, bracketed
    "http://[::ffff:127.0.0.1]/a/b",         # loopback written as IPv4-mapped IPv6
    "https://[::ffff:169.254.169.254]/a/b",  # metadata written as IPv4-mapped IPv6
    "http://0.0.0.0/a/b",                    # unspecified
    "http://admin-api:8001/a/b",             # a compose neighbour — a BARE LABEL
    "http://redis/a/b",
    "http://localhost/a/b",
    "https://localhost:3000/x/y",
    "git@admin-api:owner/repo.git",          # the scp-like form reaches the same network
    "ssh://git@localhost/owner/repo.git",
]

PUBLIC = [
    "https://github.com/owner/repo.git",
    "https://gitlab.com/group/sub/repo.git",
    "http://git.example.com/owner/repo.git",
    "git@github.com:owner/repo.git",
    "ssh://git@github.com/owner/repo.git",
    "https://git.internal.example.com:8080/owner/repo.git",   # dotted: somebody configured this
    "https://token@github.com/owner/repo.git",                # userinfo is not the host
]


@pytest.mark.parametrize("url", INTERNAL)
def test_internal_host_is_refused(url):
    with pytest.raises(RepoRefError) as exc:
        assert_public_host(url)
    assert exc.value.kind == "host"


@pytest.mark.parametrize("url", PUBLIC)
def test_public_host_is_accepted(url):
    assert_public_host(url)          # does not raise


def test_ipv4_mapped_ipv6_is_not_trusted_by_its_flags():
    """REGRESSION: ``IPv6Address('::ffff:127.0.0.1').is_loopback`` is False — the flag describes ``::1``,
    not what the address maps to. The check unwraps the mapping before asking."""
    assert _host_is_internal("::ffff:127.0.0.1")
    assert _host_is_internal("[::ffff:10.0.0.1]:9000")


def test_a_dotted_private_name_is_left_alone():
    """A name with a dot is one somebody configured (a self-hosted mirror). Refusing it would break the
    self-host case and close nothing: the deployment's own neighbours answer to bare labels."""
    assert not _host_is_internal("git.internal.example.com")
    assert _host_is_internal("git-internal")


# ── the transport half: ext:: runs a command, file:// reads the disk ──────────────────────────────

REFUSED_TRANSPORTS = [
    "ext::sh -c 'curl http://169.254.169.254'",
    "ext::sh -c whoami",
    "file:///etc",
    "file:///etc/passwd",
    "git://github.com/owner/repo.git",
    "ftp://example.com/repo.git",
    "ftps://example.com/repo.git",
    "transport::whatever",
]


@pytest.mark.parametrize("url", REFUSED_TRANSPORTS)
def test_transport_we_did_not_name_is_refused(url):
    with pytest.raises(RepoRefError) as exc:
        assert_allowed_scheme(url)
    assert exc.value.kind == "scheme"


def test_a_server_path_is_not_a_repository_a_caller_may_name(tmp_path, monkeypatch):
    """A scheme-less path is a location on THIS server. Cloning one reads whatever git repo lives
    there — including another subject's workspace out of the shared store."""
    monkeypatch.delenv(repo_ref.LOCAL_ROOTS_ENV, raising=False)
    for value in (str(tmp_path), "/etc", "../../etc", "./repo", "owner/repo"):
        with pytest.raises(RepoRefError) as exc:
            assert_allowed_scheme(value)
        assert exc.value.kind == "local"


def test_a_self_hoster_can_opt_local_paths_back_in(tmp_path, monkeypatch):
    """The one legitimate scheme-less case — a self-hosted deployment attaching from a local mirror —
    is an explicit opt-in naming the roots, not the default."""
    allowed = tmp_path / "mirrors"
    allowed.mkdir()
    (allowed / "proj").mkdir()
    monkeypatch.setenv(repo_ref.LOCAL_ROOTS_ENV, str(allowed))

    assert_allowed_scheme(str(allowed / "proj"))          # inside the configured root → fine
    with pytest.raises(RepoRefError):                     # …and only inside it
        assert_allowed_scheme(str(tmp_path / "elsewhere"))
    with pytest.raises(RepoRefError):                     # …traversal out of it is still out of it
        assert_allowed_scheme(str(allowed / ".." / "elsewhere"))


def test_empty_repo_is_not_a_reference(monkeypatch):
    """``repo`` omitted means "swap back to the seed" — it never reaches git, so it is not refused."""
    monkeypatch.delenv(repo_ref.LOCAL_ROOTS_ENV, raising=False)
    assert_fetchable(None)
    assert_fetchable("")
    assert_fetchable("   ")


# ── the gate runs before any process exists ───────────────────────────────────────────────────────

@pytest.mark.parametrize("url", INTERNAL)
def test_clone_refuses_an_internal_host_before_any_subprocess(url, tmp_path, no_subprocess):
    """The reported sink. ``_git_clone`` refuses without starting git — asserted by the absence of a
    subprocess, not by the message."""
    with pytest.raises(RepoRefError):
        workspace_attach._git_clone(url, "main", tmp_path / "dest")
    assert no_subprocess == []


@pytest.mark.parametrize("url", REFUSED_TRANSPORTS + ["http://127.0.0.1/a/b", "file:///etc"])
def test_the_api_gate_refuses_before_any_subprocess(url, no_subprocess, monkeypatch):
    """``assert_fetchable`` is what the routes call. Both halves, one entry point, no process."""
    monkeypatch.delenv(repo_ref.LOCAL_ROOTS_ENV, raising=False)
    with pytest.raises(RepoRefError):
        assert_fetchable(url)
    assert no_subprocess == []


# ── the second enforcement, inside git itself ─────────────────────────────────────────────────────

def test_transports_are_pinned_to_what_the_url_needs():
    """A transport is carried only when the reference ITSELF named it, so nothing can be reached as a
    downgrade target — and ``ext`` is named by nothing, so it appears in no list at all.

    This is not the gate on what a CALLER may ask for (that is ``assert_allowed_scheme``, which
    refuses ``file://`` outright); it is the narrowing applied to whatever reference we did accept."""
    assert git_transports_for("https://github.com/o/r.git") == ("https", "ssh")
    assert git_transports_for("git@github.com:o/r.git") == ("https", "ssh")
    assert git_transports_for("ssh://git@github.com/o/r.git") == ("https", "ssh")
    assert git_transports_for("http://git.example.com/o/r.git") == ("https", "ssh", "http")
    assert git_transports_for("file:///srv/mirror.git") == ("https", "ssh", "file")
    assert git_transports_for("ext::sh -c whoami") == ("https", "ssh")
    assert "file" in git_transports_for("/srv/mirrors/proj")
    for url in ("https://github.com/o/r.git", "git@github.com:o/r.git", "ext::sh -c whoami"):
        assert "ext" not in git_transports_for(url)
        assert "file" not in git_transports_for(url)


def test_pinned_env_sets_the_allow_list_and_keeps_the_scrub():
    """``GIT_ALLOW_PROTOCOL`` is git's own enforcement of the same list — and it overrides any
    ``protocol.<name>.allow`` config, which is why this is the single mechanism used."""
    env = pinned_git_env("https://github.com/o/r.git", GIT_ASKPASS="true")
    assert env["GIT_ALLOW_PROTOCOL"] == "https:ssh"
    assert env["GIT_ASKPASS"] == "true"
    assert "GIT_DIR" not in env                     # the scrub still happens


def test_git_itself_refuses_the_unnamed_transports(tmp_path):
    """End-to-end against the real ``git``: the pinned env is what stops a transport, not our regex.

    An ``https`` reference cannot reach ``ext`` (a shell command) or ``file`` (this host's disk), so a
    redirect into either is refused by git before anything is fetched."""
    real = tmp_path / "real.git"          # a REAL repo, so the refusal is the transport and not "no such repo"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(real)], check=True, capture_output=True)

    env = pinned_git_env("https://github.com/o/r.git")
    for url in ("ext::sh -c whoami", "file:///etc", f"file://{real}", str(real)):
        proc = subprocess.run(["git", "clone", "--quiet", "--", url, str(tmp_path / "out")],
                              capture_output=True, text=True, env=env)
        assert proc.returncode != 0
        assert "not allowed" in (proc.stderr or ""), f"{url} was not refused: {proc.stderr!r}"


def test_ext_is_refused_even_under_a_local_references_pin(tmp_path):
    """The widest pin any reference can produce still carries no ``ext`` — the transport that runs a
    shell command is reachable from nothing."""
    env = pinned_git_env(str(tmp_path))
    proc = subprocess.run(["git", "clone", "--quiet", "--", "ext::sh -c whoami", str(tmp_path / "o")],
                          capture_output=True, text=True, env=env)
    assert proc.returncode != 0 and "not allowed" in (proc.stderr or "")


def test_a_local_path_still_clones_under_its_own_pin(tmp_path):
    """The fixtures clone from a local bare repo. The pin is derived from the URL, so that keeps
    working while a REMOTE url can never downgrade into it."""
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (origin / "MARK").write_text("x")
    run("add", "-A")
    run("commit", "-q", "-m", "seed")

    dest = tmp_path / "dest"
    workspace_attach._git_clone(str(origin), "main", dest)
    assert (dest / "MARK").read_text() == "x"
