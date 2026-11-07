"""Lite warns at start when zero-login could reach the network.

`bin/zero-login-warning <supervisord.conf>` prints a banner on stderr (the container log) whenever
VEXA_TERMINAL_ZERO_LOGIN is on and the terminal's in-container bind is not loopback; the entrypoint
runs it on the rendered supervisor config before supervisord starts.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"
HELPER = LITE / "bin" / "zero-login-warning"
SHIPPED = LITE / "supervisord.conf"


def _run(conf: Path, zero_login: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "VEXA_TERMINAL_ZERO_LOGIN"}
    if zero_login is not None:
        env["VEXA_TERMINAL_ZERO_LOGIN"] = zero_login
    return subprocess.run([sys.executable, str(HELPER), str(conf)], capture_output=True, text=True, env=env)


def _with_terminal_host(tmp_path: Path, host: str | None) -> Path:
    text = SHIPPED.read_text()
    head, tail = text.split("[program:terminal]", 1)
    if host is None:
        tail = re.sub(r'(^environment=.*?)HOST="[^"]*",', r"\1", tail, count=1, flags=re.M)
    else:
        tail = re.sub(r'(^environment=.*?)HOST="[^"]*"', rf'\1HOST="{host}"', tail, count=1, flags=re.M)
    out = tmp_path / "supervisord.conf"
    out.write_text(head + "[program:terminal]" + tail)
    return out


def test_the_shipped_lite_warns_because_its_terminal_listens_on_every_interface():
    run = _run(SHIPPED, "true")
    assert run.returncode == 0 and run.stdout == ""
    err = run.stderr
    assert "zero-login is ON" in err and "0.0.0.0:3001" in err
    assert "signed in as this instance's user" in err                    # what is exposed
    assert "If this port is" in err and "published beyond localhost" in err
    assert "VEXA_TERMINAL_ZERO_LOGIN=false" in err                         # fix 1
    assert "-p 127.0.0.1:3001:3001" in err                                 # fix 2
    assert len(err.splitlines()) >= 8                                      # a banner, not a line


@pytest.mark.parametrize("value", [None, "", "false", "1", "yes", "off"])
def test_no_banner_when_zero_login_is_off(value):
    run = _run(SHIPPED, value)
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")


@pytest.mark.parametrize("value", ["TRUE", " true "])
def test_zero_login_is_read_like_the_terminal_reads_it(value):
    assert "zero-login is ON" in _run(SHIPPED, value).stderr


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost"])
def test_no_banner_when_the_terminal_listens_on_loopback(tmp_path, host):
    run = _run(_with_terminal_host(tmp_path, host), "true")
    assert (run.returncode, run.stderr) == (0, "")


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.5", "::", "lite.example.internal"])
def test_a_lan_or_wildcard_bind_warns(tmp_path, host):
    err = _run(_with_terminal_host(tmp_path, host), "true").stderr
    assert f"listens on {host}:3001" in err


def test_a_terminal_with_no_host_named_takes_server_mjs_default_and_warns(tmp_path):
    conf = _with_terminal_host(tmp_path, None)
    assert 'HOST="' not in conf.read_text().split("[program:terminal]", 1)[1].split("\n[", 1)[0]
    assert "listens on 0.0.0.0" in _run(conf, "true").stderr
    server = (ROOT / "clients" / "terminal" / "server.mjs").read_text()
    assert 'process.env.HOST || "0.0.0.0"' in server


def test_an_unreadable_config_still_warns_and_never_fails(tmp_path):
    run = _run(tmp_path / "missing.conf", "true")
    assert run.returncode == 0 and "zero-login is ON" in run.stderr


def test_the_entrypoint_runs_it_on_the_rendered_config_and_the_image_ships_it():
    entry = (LITE / "entrypoint.sh").read_text()
    render = entry.index("render-supervisord /etc/supervisor/conf.d/vexa.conf /run/vexa/supervisord.conf")
    warn = entry.index("python3 /usr/local/bin/zero-login-warning /run/vexa/supervisord.conf")
    assert render < warn < entry.index('exec "$@"')
    assert "deploy/lite/bin/zero-login-warning" in (LITE / "Dockerfile.lite").read_text()
