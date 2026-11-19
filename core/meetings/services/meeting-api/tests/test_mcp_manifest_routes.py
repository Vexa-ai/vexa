"""The routes behind the meetings domain's MCP tools, as meeting-api actually publishes them.

`core/meetings/mcp.tools.v1.json` binds each tool to a route; the assembled MCP derives the tool's
schema and description from that route's OpenAPI. `core/meetings/mcp.tools.v1.openapi.json` is the
slice of meeting-api's `/openapi.json` those routes occupy, committed so the MCP service's tests
assemble the meetings tools against what meeting-api really serves. This test keeps the slice true —
same shape as `core/agent/tests/test_mcp_manifest_routes.py`.

Regenerate after changing a tool route: VEXA_REGEN_MCP_OPENAPI=1 uv run pytest -q tests/test_mcp_manifest_routes.py
"""
from __future__ import annotations

import json
import os
import pathlib

from fastapi.testclient import TestClient

from meeting_api.app import create_app

MEETINGS = pathlib.Path(__file__).resolve().parents[3]
MANIFEST = json.loads((MEETINGS / "mcp.tools.v1.json").read_text())
SLICE = MEETINGS / "mcp.tools.v1.openapi.json"


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
    spec = create_app().openapi()
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


def test_every_tool_route_exists_on_meeting_api():
    spec = create_app().openapi()
    for tool in MANIFEST["tools"]:
        assert tool["route"]["method"].lower() in spec["paths"].get(tool["route"]["path"], {}), tool["name"]


def test_every_tool_says_what_it_does():
    """A route with no docstring publishes FastAPI's title as the whole tool description."""
    for path, ops in live_slice()["paths"].items():
        for method, op in ops.items():
            assert (op.get("description") or "").strip(), f"{method.upper()} {path} has no description"


def test_meeting_api_serves_the_manifest():
    r = TestClient(create_app()).get("/.well-known/mcp-tools.json")
    assert r.status_code == 200
    assert r.json() == MANIFEST


def test_the_committed_route_slice_is_what_meeting_api_serves():
    live = live_slice()
    if os.environ.get("VEXA_REGEN_MCP_OPENAPI"):
        SLICE.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
    assert json.loads(SLICE.read_text()) == live, (
        "core/meetings/mcp.tools.v1.openapi.json is stale — regenerate it with "
        "VEXA_REGEN_MCP_OPENAPI=1 uv run pytest -q tests/test_mcp_manifest_routes.py")
