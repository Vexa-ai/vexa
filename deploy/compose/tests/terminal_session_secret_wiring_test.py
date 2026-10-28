"""The terminal's session secret is generated at install on compose (offline — no stack, no docker).

admin-api and the terminal both hold VEXA_TERMINAL_SESSION_SECRET (the terminal presents it to
admin-api's POST /internal/sessions). Both refuse to start without it, so compose passes the SAME
`.env` value to both with no literal default, `.env.example` leaves it empty, and `mint-dev-env.sh`
mints it the way it mints INTERNAL_API_SECRET — and keeps it, never printing it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from workspace_store_wiring_test import COMPOSE, ROOT, _env, _service

MINT = ROOT / "deploy" / "compose" / "mint-dev-env.sh"
EXAMPLE = ROOT / "deploy" / "compose" / ".env.example"
KEY = "VEXA_TERMINAL_SESSION_SECRET"


def _value(env_file: Path) -> str:
    (value,) = re.findall(rf"^{KEY}=(.*)$", env_file.read_text(), flags=re.M)
    return value


def _mint(env_file: Path) -> str:
    return subprocess.run(["bash", str(MINT), str(env_file)], check=True, capture_output=True, text=True).stdout


def test_admin_api_and_the_terminal_read_the_same_env_value_with_no_default():
    assert _value(EXAMPLE) == ""
    for service in ("admin-api", "terminal"):
        assert _env(_service(service), KEY) == "${VEXA_TERMINAL_SESSION_SECRET:-}", service
    assert not re.search(r"\$\{VEXA_TERMINAL_SESSION_SECRET:-[^}$]", COMPOSE.read_text())


def test_mint_dev_env_mints_it_and_keeps_it(tmp_path):
    env_file = tmp_path / ".env"
    out = _mint(env_file)
    minted = _value(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", minted)
    assert f"minted {KEY}" in out and minted not in out
    out = _mint(env_file)
    assert _value(env_file) == minted, "a minted secret is kept on the next run"
    assert f"kept {KEY}" in out


def test_an_older_env_without_the_key_gets_one_appended(tmp_path):
    env_file = tmp_path / ".env"
    _mint(env_file)
    text = re.sub(rf"^{KEY}=.*\n", "", env_file.read_text(), flags=re.M)
    env_file.write_text(text)
    out = _mint(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", _value(env_file)) and f"appended {KEY}" in out
