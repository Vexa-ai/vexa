"""The dispatch signing key has no published default on compose (offline — no stack, no docker).

agent-api signs each dispatch's identity token with ``VEXA_DISPATCH_SIGNING_KEY``. Compose used to
default it to ``dev-dispatch-signing-key``, a value published in this repository. Now compose passes
it through with no default, ``mint-dev-env.sh`` mints it, and an ``.env`` still holding the published
value gets a new one.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from workspace_store_wiring_test import ROOT, _env, _service

MINT = ROOT / "deploy" / "compose" / "mint-dev-env.sh"
KEY = "VEXA_DISPATCH_SIGNING_KEY"
PUBLISHED = "dev-dispatch-signing-key"


def _value(env_file: Path) -> str:
    (value,) = re.findall(rf"^{KEY}=(.*)$", env_file.read_text(), flags=re.M)
    return value


def test_compose_passes_the_key_through_with_no_default():
    assert _env(_service("agent-api"), KEY) == "${VEXA_DISPATCH_SIGNING_KEY:-}"
    assert PUBLISHED not in (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text()
    assert _value(ROOT / "deploy" / "compose" / ".env.example") == ""


def test_mint_dev_env_mints_the_key_and_replaces_the_published_one(tmp_path):
    env_file = tmp_path / ".env"
    subprocess.run(["bash", str(MINT), str(env_file)], check=True, capture_output=True)
    minted = _value(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", minted)

    subprocess.run(["bash", str(MINT), str(env_file)], check=True, capture_output=True)
    assert _value(env_file) == minted, "a minted key is kept on the next run"

    env_file.write_text(env_file.read_text().replace(f"{KEY}={minted}", f"{KEY}={PUBLISHED}"))
    subprocess.run(["bash", str(MINT), str(env_file)], check=True, capture_output=True)
    replaced = _value(env_file)
    assert replaced != PUBLISHED and re.fullmatch(r"[0-9a-f]{64}", replaced)
