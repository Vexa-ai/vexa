"""Golden conformance (L1) — the Python master builder ≡ the recording.v1 goldens.

The cloud meeting-api's ``build_recording_master`` must reproduce the SHARED
recording.v1 golden vectors byte-for-byte. These are the SAME vectors the Node twin
(``@vexa/recording``) is tested against in
``meetings/modules/recording/src/golden.test.ts`` — a pass on BOTH = the two
deliberate implementations are provably in sync (no drift).

Source of truth: ``meetings/modules/recording/src/contracts/golden/``. The vectors
are loaded BY PATH (walk up to the monorepo root, mirroring the agent-api consumer
test), not by importing the recording module's code — the goldens are the seam.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from meeting_api import build_recording_master

# The committed recording.v1 golden vectors (the spec, P8), relative to the repo root.
_GOLDEN_REL = "meetings/modules/recording/src/contracts/golden"


def _golden_dir() -> Path:
    """Locate the golden vectors by walking up to the monorepo root by marker."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _GOLDEN_REL
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"monorepo root with {_GOLDEN_REL} not found")


def _vector_paths() -> list[Path]:
    return sorted(_golden_dir().glob("*.json"))


def test_golden_dir_present():
    paths = _vector_paths()
    assert paths, (
        f"no golden vectors under {_golden_dir()} "
        "(run: node meetings/modules/recording/src/contracts/golden/generate.mjs)"
    )


@pytest.mark.parametrize("vpath", _vector_paths(), ids=lambda p: p.stem)
def test_golden_master(vpath: Path):
    v = json.loads(vpath.read_text())
    chunks = [base64.b64decode(c) for c in v["chunks"]]
    master = build_recording_master(chunks, v["format"])
    # Byte-identity, proven two ways: exact length + sha256 of the full bytes.
    assert len(master) == v["master_len"], (
        f'{v["name"]}: master length {len(master)} != {v["master_len"]}'
    )
    got = hashlib.sha256(master).hexdigest()
    assert got == v["master_sha256"], (
        f'{v["name"]}: Python master diverged from the golden (got {got[:12]}…)'
    )
