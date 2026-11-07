"""Lite ships no published dispatch signing key: it is minted on first boot and kept.

agent-api signs each dispatch's identity token with VEXA_DISPATCH_SIGNING_KEY and refuses to boot on
the old default, `dev-dispatch-signing-key`, which is published in this repository. The image bakes
no value; the entrypoint takes an unset key from `bin/persisted-secret` (saved under
$VEXA_LITE_STATE_DIR, so a restart keeps it) and sets the published value aside for the kept one,
since a .env seeded from an older compose .env still carries it.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"
PUBLISHED = "dev-dispatch-signing-key"


def _block() -> str:
    """The entrypoint's dispatch-key lines, exactly as written, with the helper's image path
    pointed at the repository copy."""
    text = (LITE / "entrypoint.sh").read_text()
    block = re.search(r'^if \[ "\$\{VEXA_DISPATCH_SIGNING_KEY:-\}" = .*?^export VEXA_DISPATCH_SIGNING_KEY=.*?\n',
                      text, flags=re.S | re.M).group(0)
    return block.replace("/usr/local/bin/persisted-secret", f"{sys.executable} {LITE / 'bin' / 'persisted-secret'}")


def _run(state: Path, value=None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin"}
    if value is not None:
        env["VEXA_DISPATCH_SIGNING_KEY"] = value
    script = f'lite_state_dir="{state}"\n' + _block() + 'printf "%s" "$VEXA_DISPATCH_SIGNING_KEY"'
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True)


def test_an_unset_key_is_minted_once_and_kept(tmp_path):
    first = _run(tmp_path).stdout
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert (tmp_path / "dispatch-signing-key").read_text().strip() == first
    assert _run(tmp_path).stdout == first, "a restart keeps the key"


def test_the_published_key_is_set_aside_for_the_kept_one(tmp_path):
    kept = _run(tmp_path).stdout
    r = _run(tmp_path, PUBLISHED)
    assert r.stdout == kept
    assert "published in the Vexa repository" in r.stderr


def test_a_given_key_is_used_as_is(tmp_path):
    given = "f" * 64
    assert _run(tmp_path, given).stdout == given
    assert not (tmp_path / "dispatch-signing-key").exists()


def test_the_image_bakes_no_key():
    dockerfile = (LITE / "Dockerfile.lite").read_text()
    assert PUBLISHED not in dockerfile
    assert re.search(r"^\s+VEXA_DISPATCH_SIGNING_KEY= \\$", dockerfile, flags=re.M)
    text = (LITE / "entrypoint.sh").read_text()
    assert not re.search(r"VEXA_DISPATCH_SIGNING_KEY:-dev", text)
