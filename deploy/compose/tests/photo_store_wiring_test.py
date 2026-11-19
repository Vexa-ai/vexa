"""People's photos on compose: admin-api's OWN storage key (offline — no stack, no docker).

admin-api writes person photos under ``person-photos/`` in the recordings bucket. It signs with
``VEXA_PHOTOS_S3_*``: mint-dev-env.sh mints the pair, storage-init makes the account on the bundled
storage and scopes it to that prefix. admin-api is never handed the storage root pair (MINIO_*) or
the recordings key (S3_ACCESS_KEY / S3_SECRET_KEY).
"""
from __future__ import annotations

import re
import subprocess

from workspace_store_wiring_test import ROOT, _service

MINT = ROOT / "deploy" / "compose" / "mint-dev-env.sh"
EXAMPLE = ROOT / "deploy" / "compose" / ".env.example"


def _env(block: str, key: str):
    got = re.findall(rf"^\s+- {key}=(.*)$", block, flags=re.M)
    return got[0].strip() if got else None


def test_admin_api_holds_no_root_pair_or_recordings_key():
    api = _service("admin-api")
    for name in ("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "S3_ACCESS_KEY", "S3_SECRET_KEY",
                 "ROOT_ACCESS_KEY", "ROOT_SECRET_KEY", "BOT_S3_ACCESS_KEY", "BOT_S3_SECRET_KEY"):
        assert _env(api, name) is None, name
    assert _env(api, "VEXA_PHOTOS_S3_ACCESS_KEY") == "${VEXA_PHOTOS_S3_ACCESS_KEY:-}"
    assert _env(api, "VEXA_PHOTOS_S3_SECRET_KEY") == "${VEXA_PHOTOS_S3_SECRET_KEY:-}"
    # where the photos live is unchanged: the recordings store and bucket, so stored photos stay readable
    assert _env(api, "MINIO_ENDPOINT") == "${MINIO_ENDPOINT:-storage:9000}"
    assert _env(api, "MINIO_BUCKET") == "${MINIO_BUCKET:-vexa}"
    assert "MINIO_SECRET_KEY" not in (_env(api, "VEXA_PHOTOS_S3_SECRET_KEY") or "")


def test_storage_init_is_handed_the_photos_key_to_scope():
    si = _service("storage-init")
    assert _env(si, "VEXA_PHOTOS_S3_ACCESS_KEY") == "${VEXA_PHOTOS_S3_ACCESS_KEY:-}"
    assert _env(si, "VEXA_PHOTOS_S3_SECRET_KEY") == "${VEXA_PHOTOS_S3_SECRET_KEY:-}"
    init = (ROOT / "deploy" / "storage" / "storage_init.py").read_text()
    assert "VEXA_PHOTOS_S3_ACCESS_KEY is a storage root or recordings key" in init


def test_env_example_declares_the_photos_key_empty():
    vals = dict(re.findall(r"^(VEXA_PHOTOS_S3_[A-Z_]+)=(.*)$", EXAMPLE.read_text(), flags=re.M))
    assert vals == {"VEXA_PHOTOS_S3_ACCESS_KEY": "", "VEXA_PHOTOS_S3_SECRET_KEY": ""}


def test_mint_dev_env_mints_the_photos_key_once(tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    subprocess.run(["bash", str(MINT), str(env)], check=True, capture_output=True, text=True)
    vals = dict(re.findall(r"^(VEXA_PHOTOS_S3_[A-Z_]+)=(.*)$", env.read_text(), flags=re.M))
    assert vals["VEXA_PHOTOS_S3_ACCESS_KEY"].startswith("vexa-photos-")
    assert len(vals["VEXA_PHOTOS_S3_SECRET_KEY"]) == 64
    root = dict(re.findall(r"^(MINIO_(?:ACCESS|SECRET)_KEY)=(.*)$", env.read_text(), flags=re.M))
    assert not set(vals.values()) & set(root.values())
    out = subprocess.run(["bash", str(MINT), str(env)], check=True, capture_output=True, text=True).stdout
    again = dict(re.findall(r"^(VEXA_PHOTOS_S3_[A-Z_]+)=(.*)$", env.read_text(), flags=re.M))
    assert again == vals
    for v in vals.values():
        assert v not in out
