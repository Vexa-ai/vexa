"""Lite ships no published terminal secret: NEXTAUTH_SECRET is minted on first boot and kept.

`bin/persisted-secret <file>` prints the secret saved in <file>, minting and saving one first. The
entrypoint uses it for NEXTAUTH_SECRET when the operator gives none, so a restart keeps the same
value and no image or entrypoint default is a value anybody can read in this repository.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "deploy" / "lite" / "bin" / "persisted-secret"
PUBLISHED = ("dev-nextauth-secret", "vexa-lite-nextauth-secret", "vexa-lite-jwt-secret")


def _run(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HELPER), str(path)], capture_output=True, text=True,
                          check=True)


def test_first_use_mints_and_saves_a_strong_secret(tmp_path):
    target = tmp_path / "state" / "nextauth-secret"
    first = _run(target).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert target.read_text().strip() == first
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_later_boots_get_the_saved_secret(tmp_path):
    target = tmp_path / "nextauth-secret"
    assert _run(target).stdout == _run(target).stdout


def test_a_short_saved_value_is_replaced(tmp_path):
    target = tmp_path / "nextauth-secret"
    target.write_text("vexa-lite-nextauth-secret\n")
    out = _run(target).stdout.strip()
    assert out != "vexa-lite-nextauth-secret" and len(out) == 64
    assert target.read_text().strip() == out


def test_an_unwritable_location_still_yields_a_secret_for_this_boot(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        run = _run(locked / "nextauth-secret")
    finally:
        os.chmod(locked, stat.S_IRWXU)
    assert re.fullmatch(r"[0-9a-f]{64}", run.stdout.strip())
    assert "cannot save" in run.stderr


def test_neither_the_image_nor_the_entrypoint_carries_a_published_secret():
    for rel in ("deploy/lite/Dockerfile.lite", "deploy/lite/entrypoint.sh"):
        text = (ROOT / rel).read_text()
        for value in PUBLISHED:
            assert value not in text, f"{rel} still carries {value}"


def test_the_entrypoint_takes_an_unset_secret_from_the_helper():
    text = (ROOT / "deploy" / "lite" / "entrypoint.sh").read_text()
    assert re.search(r'^export NEXTAUTH_SECRET="\$\{NEXTAUTH_SECRET:-\$\(/usr/local/bin/persisted-secret ', text, re.M)
    dockerfile = (ROOT / "deploy" / "lite" / "Dockerfile.lite").read_text()
    assert "deploy/lite/bin/persisted-secret /usr/local/bin/" in dockerfile
