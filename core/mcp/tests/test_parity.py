"""PARITY WITH THE RIG — the 64 tools, by name, schema and docstring.

`tests/rig_surface.json` is the surface of `deploy/dogfood/rig/vexa_control_mcp.py` at
`43d824f20`, captured through the MCP SDK itself (`tools/list` and `prompts/list`), not derived
from the source. It is the contract this package replaced the rig under: every agent connected to
the running rig sees these names, these schemas and these docstrings, and a port that quietly drops
or reworded one would change what those agents are told without changing anything they could see.

DOCSTRINGS ARE COMPARED THROUGH `inspect.cleandoc`. Python 3.13 strips a docstring's common leading
whitespace at compile time and 3.12 does not, so a raw comparison measures which interpreter ran the
test rather than whether the text is the same. cleandoc removes exactly that difference and nothing
else.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import pathlib

from vexa_mcp.server import build

REF = json.loads((pathlib.Path(__file__).parent / "rig_surface.json").read_text())


def _surface():
    srv = build()
    tools = asyncio.run(srv.list_tools())
    prompts = asyncio.run(srv.list_prompts())
    return ({t.name: t for t in tools},
            [{"name": p.name, "title": getattr(p, "title", None), "description": p.description}
             for p in prompts])


def test_the_same_sixty_four_tools():
    tools, _ = _surface()
    assert len(REF["tools"]) == 64
    assert sorted(tools) == sorted(REF["tools"])


def test_every_schema_is_byte_identical():
    tools, _ = _surface()
    bad = [n for n in REF["tools"] if tools[n].input_schema != REF["tools"][n]["input_schema"]]
    assert not bad, f"input schema changed for: {bad}"


def test_every_docstring_is_the_same_text():
    tools, _ = _surface()
    bad = [n for n in REF["tools"]
           if inspect.cleandoc(tools[n].description or "")
           != inspect.cleandoc(REF["tools"][n]["description"] or "")]
    assert not bad, f"docstring changed for: {bad}"


def test_the_three_prompts_are_unchanged():
    _, prompts = _surface()
    assert prompts == REF["prompts"]


def test_the_instruction_string_lives_in_one_file_and_is_not_empty():
    """The server's instructions are the only text every client shows before any tool is called.
    They were a literal in the middle of a 5,033-line module; they are a file now."""
    from vexa_mcp import instructions
    assert len(instructions.INSTRUCTIONS) > 8000
    src = pathlib.Path(instructions.__file__).read_text()
    assert "INSTRUCTIONS = (" in src
    tools_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "vexa_mcp" / "tools"
    for f in tools_dir.glob("*.py"):
        assert "INSTRUCTIONS" not in f.read_text(), f"{f.name} carries instruction text"
