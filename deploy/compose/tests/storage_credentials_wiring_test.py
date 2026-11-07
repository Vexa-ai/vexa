"""The object-storage root pair has no published default on compose (offline — no stack, no docker).

Compose and .env.example used to default the `storage` service's root pair (and meeting-api's
MINIO_*) to `vexa-access-key` / `vexa-secret-key`, published in this repository, and the storage
service sits on the `bots` network. Now: no default anywhere; the storage service refuses an unset or
published pair at start; ``mint-dev-env.sh`` mints a pair, replaces a published one (keeping it as
LEGACY_MINIO_* for a migration from an older MinIO) and keeps a real one; and no bot or worker is ever
handed the root pair — authenticated bots get the scoped userdata account (BOT_S3_*) only.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from workspace_store_wiring_test import COMPOSE, ROOT, _env, _service

MINT = ROOT / "deploy" / "compose" / "mint-dev-env.sh"
PUBLISHED = ("vexa-access-key", "vexa-secret-key")


def _values(env_file: Path) -> dict[str, str]:
    return dict(re.findall(r"^((?:LEGACY_)?MINIO_(?:ACCESS|SECRET)_KEY)=(.*)$", env_file.read_text(), flags=re.M))


def _mint(env_file: Path) -> str:
    return subprocess.run(["bash", str(MINT), str(env_file)], check=True, capture_output=True, text=True).stdout


def test_no_compose_surface_defaults_the_storage_root():
    text = COMPOSE.read_text()
    assert not re.search(r"\$\{MINIO_(ACCESS|SECRET)_KEY:-[^}]", text)
    assert not re.search(r"=(vexa-access-key|vexa-secret-key)\b", text)
    for name in ("storage-init", "meeting-api"):
        assert _env(_service(name), "MINIO_ACCESS_KEY") == "${MINIO_ACCESS_KEY:-}"
        assert _env(_service(name), "MINIO_SECRET_KEY") == "${MINIO_SECRET_KEY:-}"
    storage = _service("storage")
    assert _env(storage, "ROOT_ACCESS_KEY") == "${MINIO_ACCESS_KEY:-}"
    assert _env(storage, "ROOT_SECRET_KEY") == "${MINIO_SECRET_KEY:-}"
    values = dict(re.findall(r"^(MINIO_(?:ACCESS|SECRET)_KEY)=(.*)$",
                             (ROOT / "deploy" / "compose" / ".env.example").read_text(), flags=re.M))
    assert values == {"MINIO_ACCESS_KEY": "", "MINIO_SECRET_KEY": ""}
    assert not re.search("|".join(PUBLISHED), (ROOT / "deploy" / "compose" / "Makefile").read_text())


def _storage_entrypoint() -> str:
    storage = _service("storage")
    script = storage.split("      - |\n", 1)[1].split("    environment:", 1)[0]
    return "\n".join(line[8:] for line in script.splitlines()).replace("$$", "$")


def _start(access: str, secret: str) -> subprocess.CompletedProcess:
    script = _storage_entrypoint().replace("mkdir -p", "echo started; exit 0; mkdir -p")
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "ROOT_ACCESS_KEY": access, "ROOT_SECRET_KEY": secret})


def test_the_storage_service_refuses_an_unset_or_published_pair():
    assert _start("vexa-" + "a" * 24, "b" * 64).stdout.strip() == "started"
    for access, secret in (("", "b" * 64), ("vexa-access-key", "b" * 64), ("vexa-" + "a" * 24, ""),
                           ("vexa-" + "a" * 24, "vexa-secret-key"), ("minioadmin", "minioadmin")):
        r = _start(access, secret)
        assert r.returncode == 1 and "started" not in r.stdout, (access, secret)
        assert "unset or a value published" in r.stderr


def test_mint_dev_env_mints_a_pair_and_keeps_it(tmp_path):
    env_file = tmp_path / ".env"
    out = _mint(env_file)
    minted = _values(env_file)
    assert re.fullmatch(r"vexa-[0-9a-f]{24}", minted["MINIO_ACCESS_KEY"])
    assert re.fullmatch(r"[0-9a-f]{64}", minted["MINIO_SECRET_KEY"])
    assert "LEGACY_MINIO_ACCESS_KEY" not in minted, "a fresh install had no older MinIO"
    assert all(v not in out for v in minted.values())
    _mint(env_file)
    assert _values(env_file) == minted


def test_mint_dev_env_replaces_a_published_pair_and_keeps_it_for_the_old_minio(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MINIO_ACCESS_KEY=vexa-access-key\nMINIO_SECRET_KEY=vexa-secret-key\n")
    _mint(env_file)
    values = _values(env_file)
    assert values["MINIO_ACCESS_KEY"] not in PUBLISHED and values["MINIO_SECRET_KEY"] not in PUBLISHED
    assert values["LEGACY_MINIO_ACCESS_KEY"] == "vexa-access-key"
    assert values["LEGACY_MINIO_SECRET_KEY"] == "vexa-secret-key"
    # an .env from before the keys existed ran on compose's old fallback: the same
    env_file.write_text("DB_USER=postgres\n")
    _mint(env_file)
    values = _values(env_file)
    assert values["LEGACY_MINIO_SECRET_KEY"] == "vexa-secret-key" and values["MINIO_SECRET_KEY"] not in PUBLISHED


def test_mint_dev_env_keeps_a_real_pair(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MINIO_ACCESS_KEY=my-own-access\nMINIO_SECRET_KEY=" + "c" * 40 + "\n")
    _mint(env_file)
    assert _values(env_file) == {"MINIO_ACCESS_KEY": "my-own-access", "MINIO_SECRET_KEY": "c" * 40}


def test_no_bot_or_worker_is_handed_the_root_pair():
    """Only the storage service, its init step and meeting-api name the root pair. The runtime (which
    spawns bots and workers) is given none, and a bot's storage reach is the scoped BOT_S3_* account,
    which storage-init refuses to equal the root key."""
    text = COMPOSE.read_text()
    holders = {m for m in re.findall(r"^  ([a-z][a-z0-9-]*):\n(?:(?!^  [a-z]).*\n)*?\s+- (?:MINIO_SECRET_KEY|ROOT_SECRET_KEY)=",
                                     text, flags=re.M)}
    assert holders == {"storage", "storage-init", "meeting-api"}, holders
    assert "MINIO_" not in _service("runtime")
    init = (ROOT / "deploy" / "storage" / "storage_init.py").read_text()
    assert "BOT_S3_ACCESS_KEY is the storage root key" in init
