"""Where a caller-supplied git remote may send this server — judged by where its NAME leads, and held
there for the connection itself (``shared.git_egress``, ``shared.gitenv.git_transport``).

DNS is replaced at the lowest level (``socket.getaddrinfo``) so the same tests read the same on a tree
that never resolved anything: there, a dotted name that leads to ``10.0.0.7`` passed the gate.

The refusal tests assert that no subprocess started: a gate that fires after git was told where to go
has protected nothing.
"""
from __future__ import annotations

import http.server
import shlex
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import repo_ref, workspace_attach
from control_plane.repo_ref import RepoRefError, assert_fetchable

ALLOW_ENV = "VEXA_GIT_ALLOWED_INTERNAL_HOSTS"
PUBLIC = "93.184.215.14"


class Dns:
    """A resolver table with a script: ``answers[name]`` is a list of answers, one per lookup; the
    last answer repeats. Every lookup is counted."""

    def __init__(self):
        self.answers: dict[str, list[list[str]]] = {}
        self.lookups: list[str] = []

    def __call__(self, host, *args, **kwargs):
        self.lookups.append(host)
        script = self.answers.get(host)
        if not script:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        ips = script.pop(0) if len(script) > 1 else script[0]
        if not ips:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (ip, 0)) for ip in ips]


@pytest.fixture
def dns(monkeypatch):
    table = Dns()
    monkeypatch.setattr(socket, "getaddrinfo", table)
    try:                                   # the suite's hermetic resolver steps aside for this file
        from shared import git_egress, ssrf
        monkeypatch.setattr(git_egress, "resolver", ssrf.resolve_host)
    except ImportError:
        pass
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    return table


@pytest.fixture
def no_subprocess(monkeypatch):
    calls: list = []

    def _boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError(f"a subprocess was started despite the gate: {args!r}")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    return calls


INTERNAL_ANSWERS = [["10.0.0.7"], ["192.168.1.20"], ["172.20.0.3"], ["100.64.1.1"],
                    ["169.254.169.254"], ["127.0.0.1"], ["fd00::5"], [PUBLIC, "10.0.0.7"]]


# ── 1. a dotted name that leads inside the deployment is refused ──────────────────────────────────

@pytest.mark.parametrize("answer", INTERNAL_ANSWERS)
@pytest.mark.parametrize("url", ["https://git.corp.example/team/repo",
                                 "ssh://git@git.corp.example/team/repo.git",
                                 "git@git.corp.example:team/repo.git"])
def test_a_name_that_resolves_inside_is_refused(url, answer, dns, no_subprocess):
    dns.answers["git.corp.example"] = [answer]
    with pytest.raises(RepoRefError) as exc:
        repo_ref.normalize(url)
    assert exc.value.kind == "host"
    with pytest.raises(RepoRefError):
        assert_fetchable(url)
    assert no_subprocess == []


def test_the_clone_refuses_an_internal_name_before_any_subprocess(dns, no_subprocess, tmp_path):
    dns.answers["git.corp.example"] = [["10.0.0.7"]]
    with pytest.raises(RepoRefError):
        workspace_attach._git_clone("https://git.corp.example/team/repo.git", "main", tmp_path / "d")
    assert no_subprocess == []


def test_a_name_that_resolves_publicly_is_accepted(dns):
    dns.answers["git.public.example"] = [[PUBLIC]]
    assert repo_ref.normalize("https://git.public.example/team/repo") == \
        "https://git.public.example/team/repo.git"
    assert_fetchable("https://git.public.example/team/repo.git")


# ── 2. the operator's allow-list admits an internal mirror, and only that ─────────────────────────

def test_a_host_on_the_operator_list_is_allowed(dns, monkeypatch):
    dns.answers["git.corp.example"] = [["10.0.0.7"]]
    dns.answers["other.corp.example"] = [["10.0.0.8"]]
    monkeypatch.setenv(ALLOW_ENV, "git.corp.example")
    assert_fetchable("https://git.corp.example/team/repo.git")
    with pytest.raises(RepoRefError):                       # the list names hosts, not "internal"
        assert_fetchable("https://other.corp.example/team/repo.git")


def test_a_network_on_the_operator_list_is_allowed(dns, monkeypatch):
    dns.answers["git.corp.example"] = [["10.20.0.7"]]
    dns.answers["db.corp.example"] = [["10.30.0.7"]]
    monkeypatch.setenv(ALLOW_ENV, "10.20.0.0/16")
    assert_fetchable("https://git.corp.example/team/repo.git")
    with pytest.raises(RepoRefError):
        assert_fetchable("https://db.corp.example/team/repo.git")


def test_the_list_never_admits_metadata_or_loopback(dns, monkeypatch):
    """A listed name is still held off the addresses that reach this host or its cloud."""
    monkeypatch.setenv(ALLOW_ENV, "git.corp.example")
    for answer in (["169.254.169.254"], ["127.0.0.1"], ["10.0.0.7", "169.254.169.254"]):
        dns.answers["git.corp.example"] = [answer]
        with pytest.raises(RepoRefError):
            assert_fetchable("https://git.corp.example/team/repo.git")


def test_a_malformed_list_admits_nothing(dns, monkeypatch):
    dns.answers["git.corp.example"] = [["10.0.0.7"]]
    monkeypatch.setenv(ALLOW_ENV, "*.corp.example")
    with pytest.raises(RepoRefError):
        assert_fetchable("https://git.corp.example/team/repo.git")


# ── 3. rebinding: the connection is checked again and pinned ──────────────────────────────────────

def test_a_rebound_record_is_refused_at_the_connection(dns, no_subprocess, tmp_path):
    """The gate saw a public address; by the time git would connect, the name leads to metadata.
    The transport resolves again, refuses, and git never starts."""
    dns.answers["flip.example"] = [[PUBLIC], [PUBLIC], ["169.254.169.254"]]
    assert_fetchable("https://flip.example/team/repo.git")          # lookup 1 (route): public
    # lookup 2 is the clone's own host check (public); lookup 3, at the connection, is metadata
    with pytest.raises(workspace_attach.CloneError):
        workspace_attach._git_clone("https://flip.example/team/repo.git", "main", tmp_path / "d")
    assert no_subprocess == []
    assert dns.lookups.count("flip.example") == 3


def test_a_name_that_only_starts_resolving_later_is_refused(dns):
    from shared.git_egress import GitEgressRefused
    from shared.gitenv import git_transport
    with pytest.raises(GitEgressRefused):
        git_transport("https://nowhere.example/team/repo.git")


def test_the_connection_is_pinned_to_the_checked_address(dns):
    """The address git dials is the one this process checked, handed to curl by name and port."""
    from shared.gitenv import git_transport
    dns.answers["git.public.example"] = [[PUBLIC]]
    t = git_transport("https://git.public.example/team/repo.git")
    assert t.config == ("-c", f"http.curloptResolve=git.public.example:443:{PUBLIC}")
    t = git_transport("http://git.public.example:8080/team/repo.git")
    assert t.config == ("-c", f"http.curloptResolve=git.public.example:8080:{PUBLIC}")
    dns.answers["v6.example"] = [["2606:4700::1111"]]
    assert git_transport("https://v6.example/a/b.git").config == \
        ("-c", "http.curloptResolve=v6.example:443:[2606:4700::1111]")


def test_an_ssh_connection_is_pinned_and_keeps_the_deploy_key(dns):
    from shared.gitenv import git_transport
    dns.answers["git.public.example"] = [[PUBLIC]]
    key = "ssh -i /tmp/k -o IdentitiesOnly=yes"
    t = git_transport("git@git.public.example:team/repo.git", ssh_command=key)
    cmd = t.env["GIT_SSH_COMMAND"]
    assert cmd.startswith(key) and f"HostName={PUBLIC}" in cmd and "HostKeyAlias=git.public.example" in cmd
    assert t.config == ()
    t = git_transport("ssh://git@git.public.example:2222/team/repo.git")
    assert "HostKeyAlias='[git.public.example]:2222'" in t.env["GIT_SSH_COMMAND"]


def test_ssh_dials_the_pinned_address(dns):
    """``ssh -G`` prints the configuration ssh would connect with: the pinned address wins over the
    name, and the host key is looked up under the name."""
    from shared.gitenv import git_transport
    dns.answers["git.public.example"] = [[PUBLIC]]
    cmd = git_transport("git@git.public.example:team/repo.git").env["GIT_SSH_COMMAND"]
    out = subprocess.run([*shlex.split(cmd), "-G", "git.public.example"], capture_output=True,
                         text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    conf = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
    assert conf["hostname"] == PUBLIC
    assert conf["hostkeyalias"] == "git.public.example"


def test_git_dials_the_pinned_address_not_the_name(tmp_path, dns):
    """End to end through ``run_git``: a name nothing resolves is dialled at the pinned address.
    The server counts the request that reached it."""
    from shared.gitexec import run_git
    hits = []

    class Server(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.headers.get("Host"))
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_port
        r = run_git(tmp_path, "-c", f"http.curloptResolve=pinned.invalid:{port}:127.0.0.1", "clone",
                    f"http://pinned.invalid:{port}/o/r.git", str(tmp_path / "out"),
                    env={"GIT_ALLOW_PROTOCOL": "http"}, timeout=30)
        assert r.returncode != 0                      # 404: nothing to clone, but it got there
        assert hits and hits[0] == f"pinned.invalid:{port}"
    finally:
        srv.shutdown()


# ── what stays refused, and what stays the operator's ─────────────────────────────────────────────

@pytest.mark.parametrize("url", ["http://10.0.0.5/a/b.git", "https://169.254.169.254/a/b.git",
                                 "http://admin-api:8001/a/b.git", "https://localhost/a/b.git"])
def test_literal_and_bare_hosts_are_refused_at_the_connection_too(url, dns):
    from shared.git_egress import GitEgressRefused
    from shared.gitenv import git_transport
    with pytest.raises(GitEgressRefused):
        git_transport(url)


def test_a_configured_provider_is_the_operators(dns, monkeypatch):
    """A host in ``VEXA_GIT_PROVIDERS`` is the operator's, wherever it resolves: not checked, not pinned."""
    from shared import git_provider
    from shared.gitenv import git_transport
    monkeypatch.setenv(git_provider.PROVIDERS_ENV,
                       '[{"name":"corp","kind":"gitlab","baseUrl":"https://gitlab.corp.example"}]')
    dns.answers["gitlab.corp.example"] = [["10.0.0.9"]]
    assert_fetchable("https://gitlab.corp.example/group/repo.git")
    assert git_transport("https://gitlab.corp.example/group/repo.git").config == ()
