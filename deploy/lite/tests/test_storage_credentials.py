"""Lite's object-storage root credentials have no published default.

The storage sidecar's root pair used to default to `vexa-access-key` / `vexa-secret-key`, published in
this repository, and every child process in the Lite container can reach the sidecar. Now:
`storage-credentials.sh` mints a pair into .env when either is unset or published (and keeps a set
one), `make up` starts the sidecar and the app with that pair and recreates a sidecar started with
another (the volume and its objects stay), and the entrypoint refuses to start with storage configured
on an unset or published pair. One refused list everywhere.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"
SCRIPT = LITE / "storage-credentials.sh"
MAKEFILE = LITE / "Makefile"
ENTRYPOINT = LITE / "entrypoint.sh"
PUBLISHED = ("vexa-access-key", "vexa-secret-key", "minioadmin")


def _values(env_file: Path) -> dict[str, str]:
    return dict(re.findall(r"^(MINIO_(?:ACCESS|SECRET)_KEY)=(.*)$", env_file.read_text(), flags=re.M))


def _mint(env_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT), str(env_file)], check=True, capture_output=True, text=True)


def test_a_pair_is_minted_kept_and_never_printed(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_TOKEN=x\n")
    out = _mint(env_file)
    minted = _values(env_file)
    assert re.fullmatch(r"vexa-[0-9a-f]{24}", minted["MINIO_ACCESS_KEY"])
    assert re.fullmatch(r"[0-9a-f]{64}", minted["MINIO_SECRET_KEY"])
    assert all(v not in out.stdout + out.stderr for v in minted.values())
    _mint(env_file)
    assert _values(env_file) == minted, "a minted pair is kept on the next run"


def test_a_published_value_is_replaced_and_a_real_one_kept(tmp_path):
    env_file = tmp_path / ".env"
    for published in PUBLISHED:
        env_file.write_text(f"MINIO_ACCESS_KEY=my-own-key-1234\nMINIO_SECRET_KEY={published}\n")
        _mint(env_file)
        values = _values(env_file)
        assert values["MINIO_ACCESS_KEY"] == "my-own-key-1234"
        assert values["MINIO_SECRET_KEY"] not in PUBLISHED and len(values["MINIO_SECRET_KEY"]) == 64


def _entrypoint_block() -> str:
    text = ENTRYPOINT.read_text()
    return re.search(r'^if \[ -n "\$MINIO_ENDPOINT" \]; then\n.*?^fi\n', text, flags=re.S | re.M).group(0)


def _start(**env) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", _entrypoint_block() + "echo started"],
                          env={"PATH": "/usr/bin:/bin", **env}, capture_output=True, text=True)


def test_the_entrypoint_refuses_storage_on_an_unset_or_published_pair():
    good = {"MINIO_ACCESS_KEY": "vexa-" + "a" * 24, "MINIO_SECRET_KEY": "b" * 64}
    assert _start(MINIO_ENDPOINT="", MINIO_ACCESS_KEY="", MINIO_SECRET_KEY="").stdout.strip() == "started"
    assert _start(MINIO_ENDPOINT="s:9000", **good).stdout.strip() == "started"
    for bad in ({"MINIO_ACCESS_KEY": ""}, {"MINIO_ACCESS_KEY": "vexa-access-key"},
                {"MINIO_SECRET_KEY": "vexa-secret-key"}, {"MINIO_SECRET_KEY": "minioadmin"}):
        r = _start(MINIO_ENDPOINT="s:9000", **{**good, **bad})
        assert r.returncode == 1 and "started" not in r.stdout, bad
        assert "unset or a value published" in r.stderr


def test_one_refused_list():
    script = re.search(r'^\s*(""\|[^)]*)\) return 0;;', SCRIPT.read_text(), flags=re.M).group(1)
    make = re.search(r"^STORAGE_PUBLISHED := (.*)$", MAKEFILE.read_text(), flags=re.M).group(1)
    entry = re.search(r'^\s*(""\|vexa-access-key[^)]*)\)$', ENTRYPOINT.read_text(), flags=re.M).group(1)
    compose = re.search(r'case "\$\$ROOT_ACCESS_KEY" in\n\s*(\S+)\)',
                        (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text()).group(1)
    assert script == make == entry == compose


def test_no_lite_surface_defaults_the_storage_root():
    makefile = MAKEFILE.read_text()
    assert re.search(r"^MINIO_ACCESS_KEY \?=\s*$", makefile, flags=re.M)
    assert re.search(r"^MINIO_SECRET_KEY \?=\s*$", makefile, flags=re.M)
    # the published pair survives only as the credentials of an older Lite's MinIO, for migrate-storage
    lines = [l for l in makefile.splitlines() if re.search("|".join(PUBLISHED), l)
             and not l.startswith(("#", "STORAGE_PUBLISHED"))]
    assert all(l.startswith("LEGACY_MINIO_") for l in lines), lines
    assert '-e ROOT_ACCESS_KEY="$$AK" -e ROOT_SECRET_KEY="$$SK"' in makefile
    assert '-e MINIO_ACCESS_KEY="$$MINIO_AK"' in makefile and '-e MINIO_SECRET_KEY="$$MINIO_SK"' in makefile
    assert "storage-credentials.sh $(ENV_FILE)" in makefile
    assert not re.search(r"MINIO_(ACCESS|SECRET)_KEY=\S", (LITE / "Dockerfile.lite").read_text())
