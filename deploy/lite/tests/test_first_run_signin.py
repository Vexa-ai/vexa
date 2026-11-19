"""Lite ships with sign-in on, and a fresh install still has a way in.

Zero-login (`VEXA_TERMINAL_ZERO_LOGIN=true`) is an explicit opt-in: unset, a visitor with no session
is anonymous. The first-run door is then a one-time sign-in link for the self-host person, printed to
the container log by `bin/provision-key.sh` once admin-api has created that person, and on demand by
`bin/vexa-signin-link` (the terminal's own minting rule, tested in clients/terminal
`signinLinkCli.test.ts`). These rows run the real entrypoint line and the real provisioner against a
fake admin-api.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"
ENTRY = LITE / "entrypoint.sh"
PROVISION = LITE / "bin" / "provision-key.sh"


def _entry_default(value: str | None) -> str:
    line = next(l for l in ENTRY.read_text().splitlines() if l.startswith("export VEXA_TERMINAL_ZERO_LOGIN="))
    env = {"PATH": os.environ["PATH"]}
    if value is not None:
        env["VEXA_TERMINAL_ZERO_LOGIN"] = value
    return subprocess.run(["sh", "-c", f'{line}; printf %s "$VEXA_TERMINAL_ZERO_LOGIN"'], env=env,
                          capture_output=True, text=True, check=True).stdout


def test_zero_login_is_off_unless_the_operator_opts_in():
    assert _entry_default(None) == "false"
    assert _entry_default("") == "false"


def test_the_explicit_opt_in_still_turns_it_on():
    assert _entry_default("true") == "true"


class _FakeAdminApi(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if self.path.startswith("/admin/users/email/"):
            return self._send(200, {"id": 7, "email": "self-host@vexa.ai"})
        self._send(404, {})

    def do_POST(self):
        if "/tokens" in self.path:
            return self._send(200, {"token": "vxa_test_token"})
        self._send(404, {})


@pytest.fixture
def admin_api():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8001), _FakeAdminApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield
    srv.shutdown()
    srv.server_close()


def _provision(tmp_path: Path, zero_login: str | None) -> str:
    """Run the real provisioner with a stub `vexa-signin-link` on PATH (the link itself is the
    terminal's rule, tested there) and no supervisorctl."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "vexa-signin-link"
    stub.write_text('#!/bin/sh\necho "http://localhost:3001/api/auth/redeem?t=STUB-$1"\n')
    stub.chmod(0o755)
    script = PROVISION.read_text().replace("/usr/local/bin/vexa-signin-link", "vexa-signin-link")
    copy = tmp_path / "provision-key.sh"
    copy.write_text(script)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "ADMIN_API_TOKEN": "admin"}
    if zero_login is not None:
        env["VEXA_TERMINAL_ZERO_LOGIN"] = zero_login
    run = subprocess.run(["bash", str(copy)], env=env, capture_output=True, text=True, timeout=60)
    return run.stdout + run.stderr


def test_a_fresh_install_prints_a_one_time_sign_in_link_and_zero_login_does_not(tmp_path, admin_api):
    """One test, three runs: the fake admin-api holds the provisioner's fixed port (8001)."""
    out = _provision(tmp_path / "off", "false")
    assert "Sign in to the terminal (one use, valid for 1 hour)" in out
    assert re.search(r"http://localhost:3001/api/auth/redeem\?t=STUB-self-host@vexa\.ai", out)
    assert "docker exec <this container> vexa-signin-link" in out
    # unset is the default, and prints it too
    assert "redeem?t=STUB-self-host@vexa.ai" in _provision(tmp_path / "unset", None)
    # with zero-login opted in, no link
    on = _provision(tmp_path / "on", "true")
    assert "self-host keys provisioned" in on and "redeem?t=" not in on


def test_the_image_ships_the_link_command_and_the_provisioner_calls_it():
    dockerfile = (LITE / "Dockerfile.lite").read_text()
    assert "deploy/lite/bin/vexa-signin-link" in dockerfile
    assert "/usr/local/bin/vexa-signin-link self-host@vexa.ai" in PROVISION.read_text()
    wrapper = (LITE / "bin" / "vexa-signin-link").read_text()
    assert "node scripts/signin-link.mjs" in wrapper and "nextauth-secret" in wrapper
