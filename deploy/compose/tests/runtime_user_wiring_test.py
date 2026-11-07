"""The Compose runtime runs as the unprivileged uid and reaches the Docker socket through the
socket's group (requirements row X1; offline — no stack, no docker).

It used to run as the image's root with DOCKER_GID defaulting to 0. mint-dev-env.sh writes the
socket's group into .env on Linux, so `make up` needs no hand edit.
"""
from __future__ import annotations

import os
import platform
import socket
import subprocess
from pathlib import Path

import pytest

from workspace_store_wiring_test import _service

MINT = Path(__file__).resolve().parents[1] / "mint-dev-env.sh"


def test_the_runtime_runs_as_10001_and_joins_the_socket_group():
    block = _service("runtime")
    assert '\n    user: "10001:10001"\n' in block, "the runtime must not run as the image's root"
    assert '- "${DOCKER_GID:-0}"' in block.split("group_add:", 1)[1].split("\n    ports:", 1)[0]
    assert "/var/run/docker.sock:/var/run/docker.sock" in block


def _mint(env_file: Path, sock: Path) -> str:
    out = subprocess.run(["bash", str(MINT), str(env_file)], env={**os.environ, "DOCKER_SOCK": str(sock)},
                         capture_output=True, text=True, check=True)
    return out.stdout


def _value(env_file: Path, key: str) -> str:
    return [l.split("=", 1)[1] for l in env_file.read_text().splitlines() if l.startswith(key + "=")][-1]


@pytest.mark.skipif(platform.system() != "Linux", reason="the socket's group is read on Linux only")
def test_mint_writes_the_socket_group_over_the_old_default(tmp_path):
    sock = tmp_path / "docker.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock))
    try:
        env_file = tmp_path / ".env"
        env_file.write_text("DOCKER_GID=0\n")
        out = _mint(env_file, sock)
        assert _value(env_file, "DOCKER_GID") == str(sock.stat().st_gid), out
        env_file.write_text("")                          # no DOCKER_GID line at all
        _mint(env_file, sock)
        assert _value(env_file, "DOCKER_GID") == str(sock.stat().st_gid)
        env_file.write_text("DOCKER_GID=4242\n")        # an operator's own value is kept
        assert "kept DOCKER_GID" in _mint(env_file, sock)
        assert _value(env_file, "DOCKER_GID") == "4242"
    finally:
        s.close()


@pytest.mark.skipif(platform.system() != "Linux", reason="the socket's group is read on Linux only")
def test_the_test_stack_gives_the_runtime_the_socket_group(tmp_path, monkeypatch):
    from conftest import _docker_gid

    sock = tmp_path / "docker.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock))
    try:
        monkeypatch.delenv("DOCKER_GID", raising=False)
        monkeypatch.setenv("DOCKER_SOCK", str(sock))
        assert _docker_gid() == str(sock.stat().st_gid)    # not 0: uid 10001 is not root
        monkeypatch.setenv("DOCKER_GID", "4242")             # an explicit value wins
        assert _docker_gid() == "4242"
    finally:
        s.close()
