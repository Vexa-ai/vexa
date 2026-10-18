"""The rig's `entity_upsert` description states the sections the renderer actually has.

The rig is a standalone file that imports nothing from agent-api (PRD 3.3), so the section list
is a LITERAL in its docstring — and that docstring is what an agent reads to decide how to fill
the call, so a drift is a wrong PAGE, not a wrong comment.

The two sides agree through `core/agent/shared/entity_sections.v1.txt`, a committed file the
renderer generates and this reads as DATA. Neither reads the other's source: a test under
`core/` that read this file would make the domains and the lane inseparable, and importing
`shared.entities` here would put agent-api's dependency tree in the rig's venv.
"""
from __future__ import annotations

import pathlib

RIG = pathlib.Path(__file__).resolve().parents[1] / "vexa_control_mcp.py"
SECTIONS = (pathlib.Path(__file__).resolve().parents[4]
            / "core" / "agent" / "shared" / "entity_sections.v1.txt")


def test_the_tool_description_carries_every_declared_section_line():
    doc = RIG.read_text()
    for line in SECTIONS.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        assert line in doc, line
