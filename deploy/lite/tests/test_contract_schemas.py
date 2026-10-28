"""The Lite image ships every contract schema agent-api's loader reads by path.

``core/agent/contracts/loader.py`` names each schema it reads as a path under the repository root it
discovers at import (``meetings/contracts/...``, ``agent/contracts/...``), and Lite roots the agent
tree at ``/app/agent``. A schema the loader reads and the image does not ship stops agent-api at
import, on every boot: the model catalog's ``models.v1`` schema was added to the loader and to the
agent-api image but not to ``Dockerfile.lite``, and Lite's agent-api never started (ADR-0043).

``test_image_by_path_reads`` covers reads relative to a module's own file; this covers the loader's,
derived from the loader itself, so the next schema it reads is checked without being named here.
Booting agent-api with the catalog unset and set is ``model-catalog-boot.sh``, against the image.
"""
from __future__ import annotations

import re

from test_image_by_path_reads import ROOT, _copies, _provided

LOADER = ROOT / "core" / "agent" / "contracts" / "loader.py"
AGENT_API_DOCKERFILE = ROOT / "core" / "agent" / "services" / "agent-api" / "Dockerfile"
SCHEMA_CONST = re.compile(r'^_\w+_SCHEMA\s*=\s*Path\("([^"]+)"\)', re.M)


def _loader_schemas() -> list[str]:
    paths = SCHEMA_CONST.findall(LOADER.read_text(encoding="utf-8"))
    assert paths, "the loader names no schema paths — this test no longer reads it right"
    return paths


def test_the_loader_still_names_the_model_catalog_schema():
    assert "agent/contracts/models.v1/models.schema.json" in _loader_schemas()


def test_lite_ships_every_schema_the_agent_loader_reads():
    copies = _copies()
    missing = [p for p in _loader_schemas() if not _provided(f"/app/agent/{p}", copies)]
    assert not missing, f"Dockerfile.lite does not put these where the agent loader reads them: {missing}"


def test_the_agent_api_image_ships_every_schema_the_loader_reads():
    text = AGENT_API_DOCKERFILE.read_text(encoding="utf-8")
    missing = [p for p in _loader_schemas() if not re.search(rf"\s\./{re.escape(p)}\s*$", text, re.M)]
    assert not missing, f"the agent-api Dockerfile does not ship: {missing}"
