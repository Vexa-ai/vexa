"""What the runtime image and Lite install, and from where (D-4, D-5 for the runtime and Lite).

The runtime's ASGI server comes from its uv.lock (a ``production`` dependency group), never from an
unlocked ``uv pip install``; uv itself is a release past the advisories fixed in 0.11.15, and Lite
takes it as a release binary checked against a pinned SHA-256 instead of piping a remote script to a
shell. Read from the files that build the images.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DF = (ROOT / "core" / "runtime" / "Dockerfile").read_text()
LITE_DF = (ROOT / "deploy" / "lite" / "Dockerfile.lite").read_text()
FIXED_UV = (0, 11, 15)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def test_the_runtime_image_installs_nothing_outside_its_lock():
    assert "uv pip install" not in RUNTIME_DF
    assert "uv sync --frozen --no-install-project --no-dev --group production" in RUNTIME_DF
    pyproject = (ROOT / "core" / "runtime" / "pyproject.toml").read_text()
    assert re.search(r'^production = \["uvicorn\[standard\]==[0-9.]+"\]$', pyproject, flags=re.M)
    lock = (ROOT / "core" / "runtime" / "uv.lock").read_text()
    assert 'production = [{ name = "uvicorn", extras = ["standard"]' in lock


def test_lites_runtime_venv_installs_nothing_outside_its_lock():
    block = LITE_DF.split("COPY core/runtime/pyproject.toml", 1)[1].split("\n# ", 1)[0]
    assert "uv pip install" not in block
    assert "--group production" in block


def test_uv_is_a_fixed_release_and_no_remote_script_runs():
    (pinned,) = re.findall(r"pip install --no-cache-dir uv==([0-9.]+)", RUNTIME_DF)
    assert _version(pinned) >= FIXED_UV
    (lite_uv,) = re.findall(r"^ARG UV_VERSION=([0-9.]+)$", LITE_DF, flags=re.M)
    assert _version(lite_uv) >= FIXED_UV
    assert not re.search(r"curl[^\n]*\|\s*(env [^|]*)?(ba)?sh\b", LITE_DF)
    assert 'sha256sum -c -' in LITE_DF.split("ARG UV_VERSION", 1)[1].split("uv --version", 1)[0]
