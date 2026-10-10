"""The admin key has no published default on compose (offline — no stack, no docker).

ADMIN_TOKEN mints an API key for any user; admin-api reads it as ADMIN_API_TOKEN, flows as
VEXA_FLOWS_ADMIN_KEY, and meeting-api derives the MeetingToken key from it. ``.env.example`` shipped it
as ``dev-admin-token``. Now: the example leaves it empty, compose passes it through with no default,
and ``mint-dev-env.sh`` mints one, replaces any value this repository published for the key (the list
the services refuse at boot, fact admin-token-placeholders) and keeps a real one — never printing it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from workspace_store_wiring_test import COMPOSE, ROOT, _env, _service

MINT = ROOT / "deploy" / "compose" / "mint-dev-env.sh"
EXAMPLE = ROOT / "deploy" / "compose" / ".env.example"
#: What compose, the chart, CI, the harnesses and the docs shipped for the admin key.
PUBLISHED = ("dev-admin-token", "changeme", "CHANGE_ME", "CHANGE-ME", "ci-admin-token", "gate-admin-token",
             "test-admin-token", "vexa-admin-token", "token", "your-secret-admin-token", "YOUR_ADMIN_API_KEY")


def _value(env_file: Path) -> str:
    (value,) = re.findall(r"^ADMIN_TOKEN=(.*)$", env_file.read_text(), flags=re.M)
    return value


def _mint(env_file: Path) -> str:
    return subprocess.run(["bash", str(MINT), str(env_file)], check=True, capture_output=True, text=True).stdout


def test_no_compose_surface_defaults_the_admin_key():
    assert _value(EXAMPLE) == ""
    text = COMPOSE.read_text()
    assert not re.search(r"\$\{ADMIN_TOKEN:-[^}$]", text), "compose re-defaulted ADMIN_TOKEN to a literal"
    assert _env(_service("admin-api"), "ADMIN_API_TOKEN") == "${ADMIN_TOKEN:-}"
    assert _env(_service("meeting-api"), "ADMIN_TOKEN") == "${ADMIN_TOKEN:-}"
    assert _env(_service("flows-api"), "VEXA_FLOWS_ADMIN_KEY") == "${VEXA_FLOWS_ADMIN_KEY:-${ADMIN_TOKEN:-}}"
    for literal in ("dev-admin-token", "CHANGE_ME"):
        assert f"={literal}" not in text and f":-{literal}" not in text


def test_mint_dev_env_mints_the_admin_key_and_keeps_it(tmp_path):
    env_file = tmp_path / ".env"
    out = _mint(env_file)
    minted = _value(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", minted)
    assert "minted ADMIN_TOKEN" in out and minted not in out
    out = _mint(env_file)
    assert _value(env_file) == minted, "a minted key is kept on the next run"
    assert "kept ADMIN_TOKEN" in out


@pytest.mark.parametrize("published", PUBLISHED)
def test_mint_dev_env_replaces_a_published_admin_key(tmp_path, published):
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_USER=postgres\nADMIN_TOKEN={published}\n")
    out = _mint(env_file)
    replaced = _value(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", replaced), published
    assert "replacing ADMIN_TOKEN (a published default)" in out
    assert replaced not in out
    assert published not in env_file.read_text().replace("DB_USER=postgres", ""), \
        "nothing keeps the published value: it opens nothing worth migrating"


def test_mint_dev_env_replaces_one_with_a_trailing_comment(tmp_path):
    """.env.example once carried ``ADMIN_TOKEN=dev-admin-token   # admin-api auth…`` on one line."""
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_TOKEN=dev-admin-token   # admin-api auth\n")
    _mint(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", _value(env_file))


def test_mint_dev_env_appends_one_to_an_env_without_the_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DB_USER=postgres\n")
    out = _mint(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", _value(env_file)) and "minted ADMIN_TOKEN" in out


def test_mint_dev_env_keeps_a_real_admin_key(tmp_path):
    env_file = tmp_path / ".env"
    real = "my-own-admin-key-" + "c" * 32
    env_file.write_text(f"ADMIN_TOKEN={real}\n")
    out = _mint(env_file)
    assert _value(env_file) == real and "kept ADMIN_TOKEN" in out and real not in out
