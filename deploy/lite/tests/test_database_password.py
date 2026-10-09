"""Lite has no default database password.

`postgres` was the image default, the entrypoint fallback and the sidecar's password, and it is
published in this repository. The entrypoint now refuses to start on an unset or published
DB_PASSWORD (the values compose's postgres refuses), the image bakes none, and `make up` mints one
into .env, sets it on the postgres sidecar and hands the same value to the app.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"


def _refusal_block() -> str:
    """The entrypoint's DB_PASSWORD check, exactly as written."""
    text = (LITE / "entrypoint.sh").read_text()
    return re.search(r'^case "\$\{DB_PASSWORD:-\}" in\n.*?^esac\n', text, flags=re.S | re.M).group(0)


def _run(value):
    env = {"PATH": "/usr/bin:/bin"} if value is None else {"PATH": "/usr/bin:/bin", "DB_PASSWORD": value}
    return subprocess.run(["bash", "-c", _refusal_block() + "echo started"], env=env,
                          capture_output=True, text=True)


def test_the_entrypoint_refuses_an_unset_or_published_password():
    for value in (None, "", "postgres", "changeme", "secret", "password"):
        r = _run(value)
        assert r.returncode == 1 and "started" not in r.stdout, value
        assert "DB_PASSWORD is unset or a value published" in r.stderr
    assert _run("a" * 64).stdout.strip() == "started"


def test_the_refused_values_are_composes():
    compose = re.search(r'case "\$\$POSTGRES_PASSWORD" in\n\s*(\S+)\)', COMPOSE.read_text()).group(1)
    lite = re.search(r"^\s*(\S+)\)$", _refusal_block(), flags=re.M).group(1)
    assert lite == compose


def test_no_lite_surface_carries_a_literal_password():
    text = (LITE / "entrypoint.sh").read_text()
    assert not re.search(r"DB_PASSWORD:-[^}]", text)
    assert not re.search(r"\bDB_PASSWORD=postgres\b", (LITE / "Dockerfile.lite").read_text())
    makefile = (LITE / "Makefile").read_text()
    assert not re.search(r"(DB_PASSWORD|POSTGRES_PASSWORD)=postgres\b", makefile)
    assert '-e POSTGRES_PASSWORD="$$DB_PW"' in makefile and '-e DB_PASSWORD="$$DB_PW"' in makefile
    assert "ALTER USER postgres PASSWORD" in makefile
