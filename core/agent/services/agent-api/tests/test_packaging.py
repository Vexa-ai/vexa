"""Packaging-integrity tests for the agent-api image.

This service dir is the PACKAGING shell around the control plane — the code itself lives
in core/agent/control_plane (the Dockerfile copies it in; src/agent_api is vestigial).
What can break here is the packaging: a repo-side move/rename of anything the Dockerfile
COPYs silently breaks the image build long after the code suites went green. These tests
pin that seam. (They also give this package a real pytest collection — an empty tests/
made gate:python fail with "no tests ran".)
"""
from __future__ import annotations

import re
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[3]          # core/agent/services/agent-api -> repo root
DOCKERFILE = SERVICE_DIR / "Dockerfile"


def _copy_sources() -> list[str]:
    """Every COPY source path in the Dockerfile (build context = repo root)."""
    sources: list[str] = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("COPY"):
            continue
        parts = [p for p in line.split()[1:] if not p.startswith("--")]
        sources.extend(parts[:-1])           # last token is the destination
    return sources


def test_every_dockerfile_copy_source_exists_in_repo():
    sources = _copy_sources()
    assert sources, "Dockerfile has no COPY lines — packaging rewritten? update this test"
    missing = [s for s in sources if not (REPO_ROOT / s).exists()]
    assert not missing, f"Dockerfile COPYs paths that no longer exist in the repo: {missing}"


def test_entrypoint_module_is_packaged():
    """CMD boots control_plane.api:app — the control_plane package must be COPYed in and
    the module it names must exist at the source location."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    m = re.search(r'CMD \["uvicorn", "([\w.]+):app"', text)
    assert m, "Dockerfile CMD no longer boots a uvicorn app — update this test"
    module = m.group(1)                       # e.g. control_plane.api
    top = module.split(".")[0]
    assert f"./{top}" in text or f" {top}" in text.replace("core/agent/", ""), (
        f"CMD boots {module} but the Dockerfile never COPYs '{top}'"
    )
    assert (REPO_ROOT / "core/agent" / (module.replace(".", "/") + ".py")).exists()
