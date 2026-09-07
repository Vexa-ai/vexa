"""The rig's `entity_upsert` description states the connection shape the writer actually reads.

Same mechanism, and the same argument, as `test_entity_card_sections.py`: the rig is a standalone
file that imports nothing from agent-api (PRD 3.3), so the shape is a LITERAL in its docstring —
and that docstring is what an agent reads to decide how to fill the call.

It has a bill behind it. On 2026-09-06 the description gave the shape by example only, a live agent
filled `connections=[{"from": …, "type": …}]`, and the writer answered `KeyError: 'name'` → 500
(Vexa-ai/vexa#1589). The keys are now stated, in a line generated from the tuple the writer reads,
carried across the two trees by `core/agent/shared/entity_connection.v1.txt` as DATA. Neither side
reads the other's source.
"""
from __future__ import annotations

import pathlib

RIG = pathlib.Path(__file__).resolve().parents[1] / "vexa_control_mcp.py"
SHAPE = (pathlib.Path(__file__).resolve().parents[4]
         / "core" / "agent" / "shared" / "entity_connection.v1.txt")


def test_the_tool_description_carries_every_declared_connection_line():
    doc = RIG.read_text()
    for line in SHAPE.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        assert line in doc, line


def test_the_description_says_which_argument_the_source_gate_reads():
    """The second half of the same incident: the agent had written `— source: …` at the end of every
    fact, and nothing anywhere said that the gate is the `source` ARGUMENT and reads nothing else."""
    doc = RIG.read_text()
    assert "THIS ARGUMENT IS THE GATE" in doc
    assert "does not satisfy it and is not read" in doc
