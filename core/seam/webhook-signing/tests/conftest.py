"""L3 SEAM wiring: the three services' `src` trees on sys.path so one venv imports the REAL admin-api,
gateway and meeting-api. Import direction is preserved: the seam imports each service; no service
imports the seam.

Postgres: `SEAM_PG_URL` (a sync `postgresql+psycopg://` URL to a throwaway database) when set,
otherwise an ephemeral testcontainers Postgres; with neither, the seam SKIPS.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _core_root() -> Path:
    rel = Path("gateway") / "services" / "gateway" / "src"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_dir():
            return parent
    raise FileNotFoundError("monorepo `core` root not found")


_ROOT = _core_root()
for p in (
    _ROOT / "identity" / "services" / "admin-api" / "src",
    _ROOT / "gateway" / "services" / "gateway" / "src",
    _ROOT / "meetings" / "services" / "meeting-api" / "src",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _docker_ok() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0


@pytest.fixture(scope="session")
def pg_url():
    explicit = os.getenv("SEAM_PG_URL")
    if explicit:
        yield explicit
        return
    if not _docker_ok():
        pytest.skip("no SEAM_PG_URL and no docker daemon for an ephemeral Postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()
