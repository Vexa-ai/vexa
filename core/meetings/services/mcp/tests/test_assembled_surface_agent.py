"""END TO END — the agent domain's manifest becomes tools an agent sees in `tools/list`.

Mirrors `test_assembled_surface.py` (flows): the manifest used here is THE FILE IN THE REPO —
`core/agent/mcp.tools.v1.json`, the same one agent-api serves at `/.well-known/mcp-tools.json`
(`control_plane/routers/health.py`). If that file stops being assemblable, this fails.

`AGENT_OPENAPI` is `core/agent/mcp.tools.v1.openapi.json`: the slice of agent-api's live
`/openapi.json` the manifest's routes occupy, regenerated from `create_app().openapi()` and held
true by `core/agent/tests/test_mcp_manifest_routes.py`. So the tools assembled here are bound
against what agent-api really serves — every argument, type and description — not a hand copy.

Every agent route behind a tool takes a NAMED body model, because a bare `body: dict` publishes no
properties for `bind.py` to derive arguments from — which is what kept the page verbs, the claim
book and the Highlight scan off this edge until their routes were typed.
"""
from __future__ import annotations

import json
import pathlib

import httpx

from vexa_mcp import create_app
from vexa_mcp.manifest import CONTRACT

REPO = pathlib.Path(__file__).resolve().parents[5]
AGENT_MANIFEST = json.loads((REPO / "core" / "agent" / "mcp.tools.v1.json").read_text())

AGENT_OPENAPI = json.loads((REPO / "core" / "agent" / "mcp.tools.v1.openapi.json").read_text())
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
    tool = next(t for t in app.state.mcp.tools if t.name == "gmail_search")
    route = AGENT_OPENAPI["paths"]["/api/connections/gmail/search"]["post"]
    assert route["description"].splitlines()[0] in (tool.description or "")


def test_no_assembled_agent_tool_takes_a_credential_argument():
    """PRD 40.8 — one authentication path into this edge: a bearer header, session-bound."""
    app = _boot(AGENT_API_URL="http://agent")
    banned = {"token", "api_key", "apikey", "access_token", "password", "secret"}
    for t in app.state.mcp.tools:
        props = set(((t.inputSchema if hasattr(t, "inputSchema") else t.input_schema) or {})
                    .get("properties", {}))
        assert not (props & banned), f"{t.name} takes {sorted(props & banned)}"


def test_agent_beside_flows_is_twentythree_plus_the_agent_tools():
    """The seam this manifest closes: the assembled edge serves 23 tools with the agent domain
    PRESENT but none of the agent's own (14 built-in + flows' 9 — 7 query/path tools plus
    `flows_submit`/`flow_lifecycle`, now bindable via `requestBody`). Wiring this manifest in
    brings that to 23 + len(agent's tools) once flows is deployed too — the number the issue that
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
        "/flows/{name}/{version}/{action}": {"post": {"summary": "flow_lifecycle", "parameters": []}},
    }}
    flows_openapi["paths"]["/flows"]["post"] = {"summary": "flows_submit", "requestBody": {
        "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/FlowSubmission"}}}}}
    flows_openapi["components"] = {"schemas": {"FlowSubmission": {
        "type": "object", "title": "FlowSubmission",
        "properties": {
            "name": {"type": "string"}, "on_event": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "params": {"type": "object"}, "activate": {"type": "boolean", "default": True}}}}}

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
                     "AGENT_API_URL": "http://agent", "VEXA_FLOWS_API_KEY": "test-operator-key"},
        assembly_transport=httpx.MockTransport(handler),
    )
    names = {t.name for t in app.state.mcp.tools}
    n_agent = len(AGENT_MANIFEST["tools"])
    assert len(names) == 23 + n_agent, (
        f"expected 23 + {n_agent} = {23 + n_agent} tools with flows+agent both present, got "
        f"{len(names)}")


def test_connections_are_owned_only_by_agent_and_absent_without_it():
    names = {'connection_request', 'connections_status', 'gmail_search', 'calendar_events',
             'secret_service_call', 'current_time', 'chat_name'}
    assert names <= {t['name'] for t in AGENT_MANIFEST['tools']}
    assert names.isdisjoint({t.name for t in _boot().state.mcp.tools})
    app = _boot(AGENT_API_URL='http://agent')
    assert all(t.domain == 'agent' for t in app.state.assembly.tools if t.name in names)


def _assembled_with(upstream):
    def discovery(request):
        if str(request.url) == 'http://agent/.well-known/mcp-tools.json':
            return httpx.Response(200, json=AGENT_MANIFEST)
        if str(request.url) == 'http://agent/openapi.json':
            return httpx.Response(200, json=AGENT_OPENAPI)
        return httpx.Response(404)
    return create_app('http://gateway.test', transport=httpx.MockTransport(upstream),
                      assembly_env={'ADMIN_API_URL': 'http://identity', 'AGENT_API_URL': 'http://agent'},
                      assembly_transport=httpx.MockTransport(discovery))


def test_connection_call_goes_through_gateway_with_caller_identity():
    from fastapi.testclient import TestClient
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={'status': 'awaiting_user', 'connection_id': 'a' * 32})
    app = _assembled_with(upstream)
    result = TestClient(app).post('/tools/connection_request', json={'provider': 'google_email'},
                                  headers={'x-api-key': 'fixture-user-key'})
    assert result.status_code == 200
    assert str(seen[-1].url) == 'http://gateway.test/agent/connections/request'
    assert seen[-1].headers['x-api-key'] == 'fixture-user-key'
    assert 'x-user-id' not in seen[-1].headers
    assert json.loads(seen[-1].content) == {'provider': 'google_email'}


def test_a_worker_s_delegation_bearer_travels_to_the_edge_like_any_other():
    """ADR-0037 §2: a cloud worker is an ordinary client of the one assembled server — its bearer
    reaches the gateway, which resolves it as the person it acts for."""
    from fastapi.testclient import TestClient
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={'utc_now': '2026-10-08T00:00:00+00:00'})
    app = _assembled_with(upstream)
    r = TestClient(app).get('/tools/current_time', headers={'Authorization': 'Bearer vxd_a.b.c'})
    assert r.status_code == 200
    assert str(seen[-1].url) == 'http://gateway.test/agent/time'
    assert seen[-1].headers['x-api-key'] == 'vxd_a.b.c'


def test_optional_object_and_list_arguments_are_published_with_their_types():
    """`setup: dict | None` and `receipts: list[...]` publish as anyOf/$ref — the edge must keep them
    an object and an array, not collapse them into strings an agent cannot fill."""
    app = _boot(AGENT_API_URL='http://agent')
    tools = {t.name: (t.inputSchema if hasattr(t, 'inputSchema') else t.input_schema) for t in app.state.mcp.tools}

    def kind(tool, arg):
        prop = tools[tool]['properties'][arg]
        return prop.get('type') or [b.get('type') for b in prop.get('anyOf', []) if b.get('type') != 'null'][0]
    assert kind('connection_request', 'setup') == 'object'
    assert kind('onboarding_research', 'receipts') == 'array'
    assert kind('onboarding_research', 'connection_ids') == 'array'
    assert kind('secret_service_call', 'parameters') == 'object'
    assert kind('gmail_search', 'limit') == 'integer'


def test_the_verbs_the_behaviour_prompts_name_reach_agent_api_through_the_gateway():
    """The page verbs, the claim book, the membership acts and the Highlight scan are what the asks
    under `behavior/` tell an agent to call. Each forwards to the gateway's `/agent/*` with the
    caller's own credential, its declared arguments in the body (lists and objects intact) or the
    query, and nothing else."""
    from fastapi.testclient import TestClient
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={})
    client = TestClient(_assembled_with(upstream))
    key = {'x-api-key': 'vxd_a.b.c'}

    client.post('/tools/entity_upsert', headers=key, json={
        'kind': 'company', 'name': 'Acme', 'facts': ['Acme builds rockets.'],
        'source': 'the call', 'fields': {'what': 'rockets'}, 'connections': ['Ana Lima']})
    assert (seen[-1].method, str(seen[-1].url)) == ('POST', 'http://gateway.test/agent/workspace/entity')
    assert json.loads(seen[-1].content) == {
        'kind': 'company', 'name': 'Acme', 'facts': ['Acme builds rockets.'], 'source': 'the call',
        'fields': {'what': 'rockets'}, 'connections': ['Ana Lima']}
    assert seen[-1].headers['x-api-key'] == 'vxd_a.b.c'

    client.put('/tools/workspace_write', headers=key,
               json={'path': 'notes/plan.md', 'content': '# Plan\n', 'slug': 'personal'})
    assert (seen[-1].method, str(seen[-1].url)) == ('PUT', 'http://gateway.test/agent/workspace/file')
    assert json.loads(seen[-1].content) == {'path': 'notes/plan.md', 'content': '# Plan\n',
                                            'slug': 'personal'}

    client.post('/tools/validate', headers=key,
                json={'verdicts': [{'id': 'c001', 'verdict': 'confirmed'}]})
    assert str(seen[-1].url) == 'http://gateway.test/agent/claims/verdicts'
    assert json.loads(seen[-1].content) == {'verdicts': [{'id': 'c001', 'verdict': 'confirmed'}]}

    client.post('/tools/transcript_terms', headers=key, json={'meeting_id': '147', 'keep': '*'})
    assert str(seen[-1].url) == 'http://gateway.test/agent/meeting/terms/scan'
    assert json.loads(seen[-1].content) == {'meeting_id': '147', 'keep': '*'}

    client.get('/tools/workspace_members', headers=key, params={'workspace_id': 'oenb-c1'})
    assert str(seen[-1].url) == 'http://gateway.test/agent/workspace/members?workspace_id=oenb-c1'
