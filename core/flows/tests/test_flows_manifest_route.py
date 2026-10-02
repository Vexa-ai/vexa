"""flows serves the manifest the gateway assembles — the running build's, not the built one.

The manifest is committed at `core/flows/mcp.tools.v1.json` and served at
`/.well-known/mcp-tools.json`. Serving it rather than baking a copy into the assembler is the whole
point: the version that answers is the version that is RUNNING, so a deployment cannot advertise a
tool this build does not serve.

Asserted against the SOURCE and the FILE, not by importing the module: importing
`flows_integrations.flows_api` opens a Postgres connection and mints an API key at import time,
which is why no test in this suite imports it.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
API = SRC / "flows_integrations" / "flows_api.py"
MANIFEST = Path(__file__).resolve().parents[1] / "mcp.tools.v1.json"


def _route(name: str) -> ast.FunctionDef:
    for node in ast.parse(API.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not a route in flows_api.py any more")


def test_the_manifest_file_exists_and_is_this_domain_s():
    doc = json.loads(MANIFEST.read_text())
    assert doc["contract"] == "mcp.tools.v1"
    assert doc["domain"] == "flows"
    assert doc["depends_on"] == ["identity"], "flows may depend on identity and nothing else"


def test_every_tool_binds_to_a_route_this_service_actually_serves():
    """The manifest is a claim about THIS service. The gateway checks it against our OpenAPI at
    boot; this checks it against our source, where it is cheaper to be wrong."""
    src = API.read_text()
    for tool in json.loads(MANIFEST.read_text())["tools"]:
        method, path = tool["route"]["method"].lower(), tool["route"]["path"]
        assert f'@app.{method}("{path}"' in src, f"{tool['name']} -> {method.upper()} {path}"


def test_no_tool_takes_a_credential_as_an_argument():
    """PRD 40.8 — one authentication path into the edge: a bearer header, session-bound."""
    banned = {"token", "api_key", "apikey", "key", "access_token", "bearer", "credential",
              "password", "secret"}
    for tool in json.loads(MANIFEST.read_text())["tools"]:
        assert not (set(a.lower() for a in tool.get("arguments") or []) & banned), tool["name"]


def test_the_manifest_route_reads_the_file_rather_than_restating_it():
    """A second copy of the tool list, in Python, would be a second thing to keep in step."""
    body = ast.unparse(_route("mcp_tools_manifest"))
    assert "_MANIFEST_PATH" in body and "read_text" in body
    assert "flows_list" not in body, "the route must not restate the manifest"


def test_the_manifest_route_is_open():
    """It names routes and argument names, never data and never a credential. An assembler that had
    to authenticate to discover the surface could not boot before identity did."""
    decs = [ast.unparse(d) for d in _route("mcp_tools_manifest").decorator_list]
    assert not any("Depends(auth)" in d or "timeline_auth" in d for d in decs)
