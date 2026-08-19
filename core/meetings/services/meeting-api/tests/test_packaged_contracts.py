"""Every contract meeting-api loads BY PATH must be COPY'd into every image that runs it.

The finding (#516): adding one more by-path schema load — ``acts.v1``, for the mid-call config
route — took the whole service down in compose. The walk-up finder resolves against the monorepo
root on a developer's disk, so 929 offline tests were green while the container crashed at import
with ``FileNotFoundError: monorepo root with meetings/contracts/acts.v1/acts.schema.json not
found``. Nothing in the tree connected "a module loads this schema" to "the packaging copies it",
so the gap was only visible by running the image.

This test is that connection. It reads the by-path loads out of the SOURCE (the ``rel = Path(...)``
lines the modules actually execute) and asserts each one is present in both packaging surfaces —
the standalone meeting-api image and the all-in-one Lite image. Adding a schema load without
packaging it now fails here, offline, in under a second.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "meeting_api"


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "core" / "meetings" / "contracts").is_dir():
            return parent
    raise FileNotFoundError("monorepo root not found")


#: ``Path("meetings") / "contracts" / "acts.v1" / "acts.schema.json"`` — the shape every by-path
#: loader in this service uses, written across one or two source lines.
_LOAD = re.compile(
    r'Path\(\s*"(meetings|runtime|agent)"\s*\)\s*/\s*"contracts"\s*/\s*'
    r'"([^"]+)"\s*/\s*"([^"]+\.schema\.json)"',
    re.S,
)


def _schemas_loaded_by_path() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for py in _SRC.rglob("*.py"):
        for m in _LOAD.finditer(py.read_text(encoding="utf-8")):
            found.add(m.groups())
    return found


def test_the_loaders_are_discoverable_at_all():
    """Negative control for this test's own regex: if the source shape changes, the assertions
    below would silently pass over an empty set."""
    loaded = _schemas_loaded_by_path()
    assert len(loaded) >= 5, f"the by-path loader scan found only {loaded} — the regex is stale"
    assert ("meetings", "acts.v1", "acts.schema.json") in loaded


@pytest.mark.parametrize(
    "dockerfile",
    [
        "core/meetings/services/meeting-api/Dockerfile",
        "deploy/lite/Dockerfile.lite",
    ],
)
def test_every_by_path_schema_is_copied_into_the_image(dockerfile: str):
    root = _repo_root()
    text = (root / dockerfile).read_text(encoding="utf-8")
    missing = sorted(
        f"core/{domain}/contracts/{version}/{name}"
        for domain, version, name in _schemas_loaded_by_path()
        if f"contracts/{version}/{name}" not in text
    )
    assert not missing, (
        f"{dockerfile} does not COPY schema(s) that meeting_api loads by path at import — the "
        f"container will crash on startup with FileNotFoundError: {missing}"
    )
