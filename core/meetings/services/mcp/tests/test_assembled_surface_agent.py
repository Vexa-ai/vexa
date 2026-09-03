"""END TO END — the agent domain's manifest becomes tools an agent sees in `tools/list`.

Mirrors `test_assembled_surface.py` (flows): the manifest used here is THE FILE IN THE REPO —
`core/agent/mcp.tools.v1.json`, the same one agent-api serves at `/.well-known/mcp-tools.json`
(`control_plane/routers/health.py`). If that file stops being assemblable, this fails.

`AGENT_OPENAPI` below is a fixture, not agent-api's live spec — but every path/method/parameter in
it is copied from a real `app.openapi()` dump of `control_plane.api.create_app(...)` (verified by
hand when this file was written: `GET /api/workspace/tree`, `/api/workspace/file`,
`/api/workspace/shared`, `/api/workspace/purpose` all publish exactly these query parameters). The
five workspace WRITE routes (`PUT /api/workspace/file`, `POST /api/workspace/entity`,
`POST /api/claims`, `POST /api/workspace/shared/new`, `POST /api/workspace/purpose`) are
deliberately NOT tools yet — they take a single JSON-body parameter, which FastAPI publishes under
`requestBody`, not `parameters`, so `bind.py` cannot derive an argument schema for them today. Same
class of gap flows' own manifest already carries (`flows_submit`/`flow_lifecycle`).
"""
from __future__ import annotations

import json
import pathlib

import httpx

from vexa_mcp import create_app
from vexa_mcp.manifest import CONTRACT

REPO = pathlib.Path(__file__).resolve().parents[5]
AGENT_MANIFEST = json.loads((REPO / "core" / "agent" / "mcp.tools.v1.json").read_text())

AGENT_OPENAPI = {"paths": {
    "/api/workspace/tree": {"get": {"summary": "Ws Tree", "parameters": [
        {"name": "hidden", "in": "query", "schema": {"type": "boolean"}},
        {"name": "slug", "in": "query", "schema": {"type": "string"}}]}},
    "/api/workspace/file": {"get": {"summary": "Ws File", "parameters": [
        {"name": "path", "in": "query", "required": True, "schema": {"type": "string"}},
        {"name": "slug", "in": "query", "schema": {"type": "string"}}]}},
    "/api/workspace/shared": {"get": {"summary": "The \"workspaces shared with me\" listing",
                                      "parameters": []}},
    "/api/workspace/purpose": {"get": {"summary": "Read a workspace's PURPOSE one-liner",
                                       "parameters": [
        {"name": "slug", "in": "query", "schema": {"type": "string"}}]}},
}}

BUILT_IN = 14


def _boot(**env):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # ONLY the agent host answers. Identity is configured (it always is) and carries no
        # manifest yet, which is the ordinary state of a domain that has not published one.
        if not url.startswith("http://agent"):
            return httpx.Response(404)
        if url.endswith("/.well-known/mcp-tools.json"):
            return httpx.Response(200, json=AGENT_MANIFEST)
        if url.endswith("/openapi.json"):
            return httpx.Response(200, json=AGENT_OPENAPI)
        return httpx.Response(404)

    return create_app(
        "http://gateway.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        assembly_env={"ADMIN_API_URL": "http://identity", **env},
        assembly_transport=httpx.MockTransport(handler),
    )


def test_the_repo_s_agent_manifest_is_the_one_that_gets_assembled():
    assert AGENT_MANIFEST["contract"] == CONTRACT and AGENT_MANIFEST["domain"] == "agent"


def test_a_deployment_with_agent_serves_the_agent_tools_beside_the_built_in_ones():
    app = _boot(AGENT_API_URL="http://agent")
    names = {t.name for t in app.state.mcp.tools}
    declared = {t["name"] for t in AGENT_MANIFEST["tools"]}
    assert declared <= names, f"missing from tools/list: {sorted(declared - names)}"
    assert len(names) == BUILT_IN + len(declared)


def test_a_deployment_without_agent_serves_exactly_the_built_in_fourteen():
    """Absent, not present-and-failing — the agent domain contributes nothing when it is not
    deployed, and that is a state an agent recovers from."""
    app = _boot()
    assert len(app.state.mcp.tools) == BUILT_IN
    assert app.state.assembly is not None and not app.state.assembly.tools


def test_an_assembled_agent_tool_carries_the_owning_route_s_description():
    """The manifest holds no description of its own — it is derived from the route's OpenAPI, so a
    tool and the route behind it cannot disagree."""
    app = _boot(AGENT_API_URL="http://agent")
    tool = next(t for t in app.state.mcp.tools if t.name == "workspace_tree")
    assert "Ws Tree" in (tool.description or "")


def test_no_assembled_agent_tool_takes_a_credential_argument():
    """PRD 40.8 — one authentication path into this edge: a bearer header, session-bound."""
    app = _boot(AGENT_API_URL="http://agent")
    banned = {"token", "api_key", "apikey", "access_token", "password", "secret"}
    for t in app.state.mcp.tools:
        props = set(((t.inputSchema if hasattr(t, "inputSchema") else t.input_schema) or {})
                    .get("properties", {}))
        assert not (props & banned), f"{t.name} takes {sorted(props & banned)}"


def test_agent_beside_flows_is_twentyone_plus_the_agent_tools():
    """The seam this manifest closes: today the assembled edge serves 21 tools with the agent
    domain PRESENT but none of the agent's own (14 built-in + flows' 7). Wiring this manifest in
    brings that to 21 + len(agent's tools) once flows is deployed too — the number the issue that
    shipped this file names as N."""
    flows_manifest_path = REPO / "core" / "flows" / "mcp.tools.v1.json"
    flows_manifest = json.loads(flows_manifest_path.read_text())
    flows_openapi = {"paths": {
        "/flows": {"get": {"summary": "flows_list", "parameters": []}},
        "/reactions": {"get": {"summary": "reactions_list", "parameters": [
            {"name": "status", "in": "query", "schema": {"type": "string"}},
            {"name": "subject", "in": "query", "schema": {"type": "string"}}]}},
        "/reactions/{reaction_id}/{verb}": {"post": {"summary": "reaction_signal", "parameters": []}},
        "/queue/waiting": {"get": {"summary": "whats_waiting", "parameters": [
            {"name": "subject", "in": "query", "schema": {"type": "string"}},
            {"name": "limit", "in": "query", "schema": {"type": "integer"}}]}},
        "/timeline": {"get": {"summary": "timeline", "parameters": [
            {"name": "subject", "in": "query", "schema": {"type": "string"}},
            {"name": "since", "in": "query", "schema": {"type": "string"}},
            {"name": "until", "in": "query", "schema": {"type": "string"}},
            {"name": "limit", "in": "query", "schema": {"type": "integer"}}]}},
        "/friction": {
            "post": {"summary": "report_friction", "parameters": [
                {"name": n, "in": "query", "schema": {"type": "string"}} for n in
                ["session", "what_i_tried", "what_happened", "severity", "meeting_id", "tool",
                 "deployment", "worker_image", "kind"]]},
            "get": {"summary": "friction_so_far", "parameters": [
                {"name": "since", "in": "query", "schema": {"type": "string"}},
                {"name": "limit", "in": "query", "schema": {"type": "integer"}}]}},
    }}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        base = "http://flows" if url.startswith("http://flows") else (
            "http://agent" if url.startswith("http://agent") else None)
        if base is None:
            return httpx.Response(404)
        manifest_doc = flows_manifest if base == "http://flows" else AGENT_MANIFEST
        openapi_doc = flows_openapi if base == "http://flows" else AGENT_OPENAPI
        if url.endswith("/.well-known/mcp-tools.json"):
            return httpx.Response(200, json=manifest_doc)
        if url.endswith("/openapi.json"):
            return httpx.Response(200, json=openapi_doc)
        return httpx.Response(404)

    app = create_app(
        "http://gateway.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        assembly_env={"ADMIN_API_URL": "http://identity", "FLOWS_API_URL": "http://flows",
                     "AGENT_API_URL": "http://agent"},
        assembly_transport=httpx.MockTransport(handler),
    )
    names = {t.name for t in app.state.mcp.tools}
    n_agent = len(AGENT_MANIFEST["tools"])
    assert len(names) == 21 + n_agent, (
        f"expected 21 + {n_agent} = {21 + n_agent} tools with flows+agent both present, got "
        f"{len(names)}")
