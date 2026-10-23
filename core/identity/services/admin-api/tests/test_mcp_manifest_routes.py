"""The routes behind identity's tools, as admin-api actually publishes them (Vexa-ai/vexa#1783).

`src/admin_api/mcp.tools.v1.json` binds each tool to a route; the assembled MCP derives the tool's
schema and description from that route's OpenAPI. `src/admin_api/mcp.tools.v1.openapi.json` is the
slice of admin-api's live `/openapi.json` those routes occupy, committed so the MCP service's tests
assemble identity's tools against what admin-api really serves. This test keeps the slice true —
same shape as `core/agent/tests/test_mcp_manifest_routes.py`.

Regenerate after changing a tool route: VEXA_REGEN_MCP_OPENAPI=1 uv run pytest -q tests/test_mcp_manifest_routes.py
"""
from __future__ import annotations

import json
import os
import pathlib

from admin_api.app.main import create_app

PKG = pathlib.Path(__file__).resolve().parents[1] / "src" / "admin_api"
MANIFEST = json.loads((PKG / "mcp.tools.v1.json").read_text())
SLICE = PKG / "mcp.tools.v1.openapi.json"


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


def test_every_tool_route_exists_on_admin_api():
    spec = create_app().openapi()
    for tool in MANIFEST["tools"]:
        assert tool["route"]["method"].lower() in spec["paths"].get(tool["route"]["path"], {}), tool["name"]


def test_the_committed_route_slice_is_what_admin_api_serves():
    live = live_slice()
    if os.environ.get("VEXA_REGEN_MCP_OPENAPI"):
        SLICE.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
    assert json.loads(SLICE.read_text()) == live, (
        "src/admin_api/mcp.tools.v1.openapi.json is stale — regenerate it with "
        "VEXA_REGEN_MCP_OPENAPI=1 uv run pytest -q tests/test_mcp_manifest_routes.py")
