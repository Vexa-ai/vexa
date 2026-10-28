"""The at-rest sealing key (MIGRATION-0015) on compose and Lite (offline — no stack, no docker).

admin-api and meeting-api seal per-user webhook signing secrets with one AES-256-GCM key, a file
of 64 hex characters apart from the database. Compose generates it once, in the `at-rest-key`
one-shot, into its own volume, and never regenerates it (a new key leaves every sealed secret
unreadable); only admin-api and meeting-api mount it, read-only, at the same path. Lite mints it on
the first boot into its state directory and hands the path to the same two programs.
"""
from __future__ import annotations

import re
import subprocess
import sys

from workspace_store_wiring_test import COMPOSE, ROOT, _env, _service

PATH = "/run/vexa-at-rest/key"
SUPERVISORD = ROOT / "deploy" / "lite" / "supervisord.conf"
ENTRYPOINT = ROOT / "deploy" / "lite" / "entrypoint.sh"
PERSISTED = ROOT / "deploy" / "lite" / "bin" / "persisted-secret"


def _services() -> list:
    return re.findall(r"^  ([a-z0-9-]+):$", COMPOSE.read_text().split("\nvolumes:\n")[0], flags=re.M)


def test_admin_api_and_meeting_api_read_the_same_key_file():
    for service in ("admin-api", "meeting-api"):
        block = _service(service)
        assert _env(block, "VEXA_AT_REST_KEY_FILE") == PATH, service
        assert "      - at-rest-key:/run/vexa-at-rest:ro" in block, service
        assert re.search(r"at-rest-key:\n\s+condition: service_completed_successfully", block), service


def test_no_other_service_mounts_the_key():
    holders = {s for s in _services() if "at-rest-key:/" in _service(s)}
    assert holders == {"admin-api", "meeting-api", "at-rest-key"}, holders
    assert re.search(r"^  at-rest-key:$", COMPOSE.read_text().split("\nvolumes:\n")[1], flags=re.M)


def _one_shot_script() -> str:
    block = _service("at-rest-key")
    return "\n".join(l[8:] for l in block.split("\n") if l.startswith("        "))


def test_the_one_shot_generates_once_and_never_replaces(tmp_path):
    block = _service("at-rest-key")
    assert "network_mode: none" in block and 'user: "0:0"' in block
    script = _one_shot_script().replace("/keys/at-rest/key", str(tmp_path / "key"))
    script = script.replace("os.chown(key, 10001, 10001)", "pass")      # not root here
    subprocess.run([sys.executable, "-c", script], check=True)
    first = (tmp_path / "key").read_text()
    assert re.fullmatch(r"[0-9a-f]{64}\n", first)
    assert (tmp_path / "key").stat().st_mode & 0o777 == 0o400
    subprocess.run([sys.executable, "-c", script], check=True)
    assert (tmp_path / "key").read_text() == first, "an existing key is never replaced"


def test_lite_mints_it_into_the_state_directory_for_the_two_programs():
    text = ENTRYPOINT.read_text()
    assert 'persisted-secret "$lite_state_dir/at-rest/key" >/dev/null' in text
    assert 'export VEXA_AT_REST_KEY_FILE="$lite_state_dir/at-rest/key"' in text
    sections = re.split(r"^\[program:([a-z0-9-]+)\]$", SUPERVISORD.read_text(), flags=re.M)
    holders = {name for name, body in zip(sections[1::2], sections[2::2]) if "VEXA_AT_REST_KEY_FILE=" in body}
    assert holders == {"admin-api", "meeting-api"}, holders


def test_persisted_secret_writes_a_64_hex_key_file(tmp_path):
    out = subprocess.run([sys.executable, str(PERSISTED), str(tmp_path / "at-rest" / "key")],
                         check=True, capture_output=True, text=True).stdout.strip()
    kept = (tmp_path / "at-rest" / "key").read_text().strip()
    assert re.fullmatch(r"[0-9a-f]{64}", kept) and out == kept
    assert bytes.fromhex(kept) and len(bytes.fromhex(kept)) == 32
