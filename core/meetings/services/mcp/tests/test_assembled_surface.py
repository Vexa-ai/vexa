"""END TO END — a domain's manifest becomes a tool an agent sees in `tools/list`.

The point of assembling rather than proxying: an assembled tool and one of this service's own
fourteen are indistinguishable to a client, because both are a route with `operation_id=<name>` that
`FastApiMCP` reads out of the same OpenAPI.

The flows manifest used here is THE FILE IN THE REPO — `core/flows/mcp.tools.v1.json`, the same one
flows-api serves at `/.well-known/mcp-tools.json`. If that file stops being assemblable, this fails.
"""
from __future__ import annotations

import json
import pathlib

import httpx

from vexa_mcp import create_app
from vexa_mcp.manifest import CONTRACT

REPO = pathlib.Path(__file__).resolve().parents[5]
FLOWS_MANIFEST = json.loads((REPO / "core" / "flows" / "mcp.tools.v1.json").read_text())

FLOWS_OPENAPI = {"paths": {
    "/flows": {"get": {"summary": "Every flow version the engine knows", "parameters": []}},
    "/reactions": {"get": {"summary": "The operator projection", "parameters": [
        {"name": "status", "in": "query", "schema": {"type": "string"}},
        {"name": "subject", "in": "query", "schema": {"type": "string"}}]}},
    "/reactions/{reaction_id}/{verb}": {"post": {"summary": "Steer one reaction", "parameters": []}},
    "/queue/waiting": {"get": {"summary": "What is waiting for this person", "parameters": [
        {"name": "subject", "in": "query", "schema": {"type": "string"}},
        {"name": "limit", "in": "query", "schema": {"type": "integer"}}]}},
    "/timeline": {"get": {"summary": "One person's day, in order", "parameters": [
        {"name": "subject", "in": "query", "schema": {"type": "string"}},
        {"name": "since", "in": "query", "schema": {"type": "string"}},
        {"name": "until", "in": "query", "schema": {"type": "string"}},
        {"name": "limit", "in": "query", "schema": {"type": "integer"}}]}},
    "/friction": {
        "post": {"summary": "Tell us what did not work", "parameters": [
            {"name": "session", "in": "query", "schema": {"type": "string"}},
            {"name": "what_i_tried", "in": "query", "schema": {"type": "string"}},
            {"name": "what_happened", "in": "query", "schema": {"type": "string"}},
            {"name": "severity", "in": "query", "schema": {"type": "string"}},
            {"name": "meeting_id", "in": "query", "schema": {"type": "string"}},
            {"name": "tool", "in": "query", "schema": {"type": "string"}},
            {"name": "deployment", "in": "query", "schema": {"type": "string"}},
            {"name": "worker_image", "in": "query", "schema": {"type": "string"}},
            {"name": "kind", "in": "query", "schema": {"type": "string"}}]},
        "get": {"summary": "Your own filed reports, newest first", "parameters": [
            {"name": "since", "in": "query", "schema": {"type": "string"}},
            {"name": "limit", "in": "query", "schema": {"type": "integer"}}]}},
    "/flows/{name}/{version}/{action}": {
        "post": {"summary": "activate | retire one flow version", "parameters": []}},
}}
FLOWS_OPENAPI["paths"]["/flows"]["post"] = {
    "summary": "Author a new flow version", "requestBody": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/FlowSubmission"}}}}}
FLOWS_OPENAPI["components"] = {"schemas": {"FlowSubmission": {
    "type": "object", "title": "FlowSubmission",
    "properties": {
        "name": {"type": "string"}, "on_event": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "params": {"type": "object"}, "activate": {"type": "boolean", "default": True}},
    "required": ["name", "on_event", "steps"]}}}

BUILT_IN = 14


def _boot(**env):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # ONLY the flows host answers. Identity is configured (it always is) and carries no manifest
        # yet, which is the ordinary state of a domain that has not published one.
        if not url.startswith("http://flows"):
            return httpx.Response(404)
        if url.endswith("/.well-known/mcp-tools.json"):
            return httpx.Response(200, json=FLOWS_MANIFEST)
        if url.endswith("/openapi.json"):
            return httpx.Response(200, json=FLOWS_OPENAPI)
        return httpx.Response(404)

    return create_app(
        "http://gateway.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        # `flows_submit`/`flow_lifecycle` are `auth: admin` — assembly refuses to boot with flows
        # deployed and no operator key held (manifest.py's own "refuse to serve a tool refused by
        # its own door" rule). Harmless when flows is absent; a test that cares about the unset case
        # overrides it back out via **env.
        assembly_env={"ADMIN_API_URL": "http://identity", "VEXA_FLOWS_API_KEY": "test-operator-key",
                     **env},
        assembly_transport=httpx.MockTransport(handler),
    )


def test_the_repo_s_flows_manifest_is_the_one_that_gets_assembled():
    assert FLOWS_MANIFEST["contract"] == CONTRACT and FLOWS_MANIFEST["domain"] == "flows"


def test_a_deployment_with_flows_serves_the_flows_tools_beside_the_built_in_ones():
    app = _boot(FLOWS_API_URL="http://flows")
    names = {t.name for t in app.state.mcp.tools}
    declared = {t["name"] for t in FLOWS_MANIFEST["tools"]}
    assert declared <= names, f"missing from tools/list: {sorted(declared - names)}"
    assert len(names) == BUILT_IN + len(declared)


def test_a_deployment_without_flows_serves_exactly_the_built_in_fourteen():
    """Absent, not present-and-failing. An agent that cannot see a tool recovers; one told a tool
    exists and handed a 502 tells the person the product is broken."""
    app = _boot()
    assert len(app.state.mcp.tools) == BUILT_IN
    assert app.state.assembly is not None and not app.state.assembly.tools


def test_an_assembled_tool_carries_the_owning_route_s_description():
    """The manifest holds no description of its own — it is derived from the route's OpenAPI, so a
    tool and the route behind it cannot disagree."""
    app = _boot(FLOWS_API_URL="http://flows")
    tool = next(t for t in app.state.mcp.tools if t.name == "flows_list")
    assert "Every flow version the engine knows" in (tool.description or "")


def test_no_assembled_tool_takes_a_credential_argument():
    """PRD 40.8 — one authentication path into this edge: a bearer header, session-bound."""
    app = _boot(FLOWS_API_URL="http://flows")
    banned = {"token", "api_key", "apikey", "access_token", "password", "secret"}
    for t in app.state.mcp.tools:
        props = set(((t.inputSchema if hasattr(t, "inputSchema") else t.input_schema) or {})
                    .get("properties", {}))
        assert not (props & banned), f"{t.name} takes {sorted(props & banned)}"


def test_a_json_body_tool_publishes_its_body_fields_in_the_tools_schema():
    """flows_submit's arguments come from `POST /flows`'s `requestBody` (`FlowSubmission`), not
    `parameters` — the whole point of this manifest's newly-added tools. The client-facing schema
    does not care which OpenAPI half an argument came from; it is flat either way."""
    app = _boot(FLOWS_API_URL="http://flows")
    tool = next(t for t in app.state.mcp.tools if t.name == "flows_submit")
    props = set(tool.inputSchema["properties"])
    assert props == {"name", "on_event", "steps", "params", "activate"}


def test_a_body_only_tool_publishes_requestbody_not_parameters_on_this_edges_own_route():
    """The mechanism that makes fastapi-mcp forward these fields as a JSON body rather than a query
    string: THIS service's own OpenAPI has to show them under `requestBody`, not `parameters` —
    that is `register.py`'s `Body(..., embed=True)` versus `Query(...)`, verified here at the level
    an agent actually receives (the assembled app's own spec), not just at the unit level."""
    app = _boot(FLOWS_API_URL="http://flows")
    spec = app.openapi()
    op = spec["paths"]["/tools/flows_submit"]["post"]
    assert "requestBody" in op
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    ref = schema.get("$ref")
    if ref:  # `embed=True` generates a named model (`Body_flows_submit`) rather than inlining it
        schema = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    body_props = set(schema["properties"])
    assert body_props == {"name", "on_event", "steps", "params", "activate"}
    assert not any(p.get("name") in body_props for p in op.get("parameters") or [])


def test_flow_lifecycle_s_path_parameters_still_publish_with_no_body_at_all():
    """flow_lifecycle takes no body (`POST /flows/{name}/{version}/{action}` reads only its three
    path segments) — the regression check that a body-less admin tool binds exactly as a body-less
    subject tool always has."""
    app = _boot(FLOWS_API_URL="http://flows")
    tool = next(t for t in app.state.mcp.tools if t.name == "flow_lifecycle")
    assert set(tool.inputSchema["properties"]) == {"name", "version", "action"}
    assert set(tool.inputSchema.get("required") or []) == {"name", "version", "action"}
