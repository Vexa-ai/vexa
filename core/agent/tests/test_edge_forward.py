"""Where the agent domain lives at the edge is declared by this domain, once per reader, identically.

The gateway reads `core/agent/routes.v1.json` and the MCP edge reads `core/agent/mcp.tools.v1.json`
(served by agent-api); both need the same fact — public `/agent/<path>` is agent-api's
`/api/<path>` — and each declares it as `forward`. This test holds the two declarations to one
value, and holds every row and tool to it.
"""
from __future__ import annotations

import json
import pathlib

AGENT = pathlib.Path(__file__).resolve().parents[1]
ROUTES = json.loads((AGENT / "routes.v1.json").read_text())
TOOLS = json.loads((AGENT / "mcp.tools.v1.json").read_text())


def test_both_manifests_declare_the_same_forward():
    assert ROUTES["forward"] == TOOLS["forward"] == {"edge_prefix": "/agent/", "upstream_prefix": "/api/"}


def test_every_edge_row_is_under_the_edge_prefix():
    edge = ROUTES["forward"]["edge_prefix"]
    assert all(r["path"].startswith(edge) for r in ROUTES["routes"])


def test_every_tool_route_is_under_the_upstream_prefix():
    upstream = TOOLS["forward"]["upstream_prefix"]
    assert all(t["route"]["path"].startswith(upstream) for t in TOOLS["tools"] if t.get("route"))


def test_the_edge_admits_the_mcp_s_re_entry_on_exactly_the_routes_the_tools_call():
    """A worker's token comes back through the edge on the MCP's re-entry, and the edge admits it
    only on a row that says `mcp_reentry`. Those rows are the tools' routes, mapped through the
    forward — one row per route a tool calls, and no row no tool calls. A tool added here without
    its row would be refused for every worker; a row left behind would admit a route nothing calls."""
    edge, upstream = ROUTES["forward"]["edge_prefix"], ROUTES["forward"]["upstream_prefix"]
    tool_routes = {(t["route"]["method"], edge + t["route"]["path"][len(upstream):])
                   for t in TOOLS["tools"] if t.get("route")}
    flagged = {(r["method"], r["path"]) for r in ROUTES["routes"] if r.get("mcp_reentry")}
    assert flagged == tool_routes, {"tool routes with no row": sorted(tool_routes - flagged),
                                    "rows no tool calls": sorted(flagged - tool_routes)}
    assert not [r for r in ROUTES["routes"] if r.get("mcp_reentry") and r.get("delegation")]
    assert not [r for r in ROUTES["routes"] if r.get("mcp_reentry") and r["path"].endswith("{path:path}")]
