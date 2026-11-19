"""F-D20 (c) — `provision-token` may not guess which stack it is talking to.

`ADMIN_API="${ADMIN_API_URL:-http://127.0.0.1:18057}"`. On a host that runs one stack that default
is right and invisible; on a host that runs several — which is every developer machine and the
dogfood box — 18057 is A DIFFERENT DEPLOYMENT'S admin-api, and this script does not read from it,
it CREATES A USER AND MINTS A SCOPED TOKEN. So the failure is not a wrong answer, it is a real
account with a live `bot,tx` token on somebody else's stack, and the operator is handed the token
as though it were theirs.

Same defect as F111, which is why flows deleted every host-port default it had
(`core/flows/tests/test_flows_doors.py`): *"a door that is not configured must REFUSE, so the
operator (or the test) is told which deployment it is missing rather than being handed somebody
else's"*. A deployment default may name a SERVICE and lives in the deploy surface — here, the
Makefile, which reads the port out of its own `.env` — never in the script.

NO DOCKER, NO NETWORK. The behavioural row runs the script with a `curl` that records being called
and fails; the point is that a correct script never reaches it.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

COMPOSE_DIR = Path(__file__).resolve().parent.parent
SCRIPT = COMPOSE_DIR / "bin" / "provision-token"
MAKEFILE = COMPOSE_DIR / "Makefile"

_HOST_PORT = re.compile(r"//(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?", re.I)


def test_the_script_carries_no_host_port_default():
    """The same regex `test_flows_doors.py` runs over flows' declaration table, over the one file
    that turns an unset door into a live token on a neighbouring stack."""
    offenders = [ln.strip() for ln in SCRIPT.read_text().splitlines()
                 if _HOST_PORT.search(ln) and not ln.lstrip().startswith("#")]
    assert offenders == [], f"host-port default(s) in provision-token: {offenders}"


def test_admin_api_url_is_required_explicit():
    """`:?` — bash's own required-explicit. Asserted structurally as well as behaviourally because
    the behavioural row can only prove the refusal happened, not that it will keep happening for
    the right reason."""
    assert re.search(r'\$\{ADMIN_API_URL:\?', SCRIPT.read_text()), \
        "ADMIN_API_URL is not required-explicit"


@pytest.fixture
def curl_landmine(tmp_path):
    """A `curl` on PATH that records being reached and then fails. A script that guesses a port
    reaches it; a script that refuses does not."""
    marker = tmp_path / "curl-was-called"
    stub = tmp_path / "curl"
    stub.write_text(f'#!/bin/sh\necho called >> "{marker}"\necho "stub curl" >&2\nexit 7\n')
    stub.chmod(0o755)
    return tmp_path, marker


def test_an_unset_admin_api_url_refuses_before_reaching_any_stack(curl_landmine):
    bin_dir, marker = curl_landmine
    env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_API_URL",)}
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["ADMIN_TOKEN"] = "an-admin-secret"
    proc = subprocess.run([str(SCRIPT)], cwd=COMPOSE_DIR, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0
    assert "ADMIN_API_URL" in proc.stderr, proc.stderr
    assert not marker.exists(), \
        "the script reached out to a stack it was never told about — that is the whole defect"


def test_a_named_admin_api_url_is_used_verbatim(curl_landmine):
    """The half a blanket refusal would break: told which deployment, it talks to that one."""
    bin_dir, marker = curl_landmine
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["ADMIN_TOKEN"] = "an-admin-secret"
    env["ADMIN_API_URL"] = "http://admin-api.test:8057"
    proc = subprocess.run([str(SCRIPT)], cwd=COMPOSE_DIR, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0            # the landmine curl failed, which is the expected end
    assert marker.exists(), "the script did not call curl at all"


def test_the_make_target_names_the_deployments_own_port():
    """A script with no default needs the DEPLOYMENT to name the door, and `make provision-token`
    is a deployment surface: `_access` already resolves it out of `.env`'s ADMIN_API_PORT, and the
    standalone target — the one every doc tells an operator to run — did not."""
    body = MAKEFILE.read_text().split("provision-token:", 1)[1].split("\n\n", 1)[0]
    assert "ADMIN_API_URL" in body, \
        "make provision-token does not pass ADMIN_API_URL, so it cannot work with the default gone"
