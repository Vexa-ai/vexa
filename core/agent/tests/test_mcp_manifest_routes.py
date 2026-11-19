"""The routes behind the agent's tools, as agent-api actually publishes them.

`core/agent/mcp.tools.v1.json` binds each tool to a route; the assembler derives the tool's schema
and description from that route's OpenAPI. `core/agent/mcp.tools.v1.openapi.json` is the slice of
agent-api's live `/openapi.json` those routes occupy, committed so the MCP service's tests assemble
the agent's tools against what agent-api really serves instead of a hand-written copy. This test is
what keeps the slice true: it is regenerated from `create_app().openapi()` and compared.

Regenerate after changing a tool route: VEXA_REGEN_MCP_OPENAPI=1 uv run pytest -q tests/test_mcp_manifest_routes.py
"""
from __future__ import annotations

import json
import os
import pathlib

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings

AGENT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = json.loads((AGENT / "mcp.tools.v1.json").read_text())
SLICE = AGENT / "mcp.tools.v1.openapi.json"


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _refs(node, out: set) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            out.add(ref.rsplit("/", 1)[-1])
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)


def live_slice() -> dict:
    spec = create_app(Dispatcher(load_settings(), _Runtime(), _Identity())).openapi()
    paths: dict = {}
    for tool in MANIFEST["tools"]:
        method, path = tool["route"]["method"].lower(), tool["route"]["path"]
        op = spec["paths"][path][method]
        paths.setdefault(path, {})[method] = {
            k: op[k] for k in ("summary", "description", "parameters", "requestBody") if k in op}
    names: set = set()
    _refs(paths, names)
    schemas = spec.get("components", {}).get("schemas", {})
    seen: set = set()
    while names - seen:
        name = sorted(names - seen)[0]
        seen.add(name)
        _refs(schemas.get(name, {}), names)
    return {"paths": paths, "components": {"schemas": {n: schemas[n] for n in sorted(seen) if n in schemas}}}


def test_every_tool_route_exists_on_agent_api():
    spec = create_app(Dispatcher(load_settings(), _Runtime(), _Identity())).openapi()
    for tool in MANIFEST["tools"]:
        assert tool["route"]["method"].lower() in spec["paths"].get(tool["route"]["path"], {}), tool["name"]


def test_the_committed_route_slice_is_what_agent_api_serves():
    live = live_slice()
    if os.environ.get("VEXA_REGEN_MCP_OPENAPI"):
        SLICE.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
    assert json.loads(SLICE.read_text()) == live, (
        "core/agent/mcp.tools.v1.openapi.json is stale — regenerate it with "
        "VEXA_REGEN_MCP_OPENAPI=1 uv run pytest -q tests/test_mcp_manifest_routes.py")
