"""BINDING — a manifest's promise checked against the service that has to keep it.

A manifest binds a NAME to a ROUTE. It carries no JSON schema and no description of its own: both
are DERIVED from the bound route's OpenAPI operation, which is the mechanism this service already
runs on for its own fourteen tools (`operation_id` on a FastAPI route, read by `FastApiMCP`). One
place to write a tool's shape means the tool and the route it forwards to cannot disagree.

So the manifest is a claim about another service, and this is where the claim is checked. A route
the domain does not serve, or an argument the operation does not take, FAILS THE BOOT — a manifest
lying about its own service is the one failure this design could otherwise hide, and it would
surface as a tool that exists in the list and 404s when an agent calls it.
"""
from __future__ import annotations

import pytest

from vexa_mcp import bind
from vexa_mcp import manifest as m

FLOWS_OPENAPI = {
    "paths": {
        "/flows": {
            "get": {"operationId": "list_flows", "summary": "Every flow version",
                    "parameters": []},
            "post": {"summary": "Author a new flow version", "requestBody": {"content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/FlowSubmission"}}}}},
        },
        "/reactions": {"get": {"summary": "The operator projection", "parameters": [
            {"name": "status", "in": "query", "schema": {"type": "string"}},
            {"name": "source_event_prefix", "in": "query", "schema": {"type": "string"}}]}},
        "/reactions/{reaction_id}/{verb}": {"post": {"summary": "Steer one reaction",
                                                     "parameters": []}},
        "/claims": {"post": {"summary": "Record a claim", "requestBody": {"content": {
            "application/json": {"schema": {
                "type": "object", "additionalProperties": True, "title": "Body"}}}}}},
        "/mixed": {"post": {"summary": "One query filter beside a JSON body", "parameters": [
            {"name": "dry_run", "in": "query", "schema": {"type": "boolean"}}],
            "requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/MixedBody"}}}}}},
    },
    "components": {"schemas": {
        "FlowSubmission": {
            "type": "object", "title": "FlowSubmission",
            "properties": {
                "name": {"type": "string", "description": "the flow's name"},
                "on_event": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "params": {"type": "object"},
                "activate": {"type": "boolean", "default": True}},
            "required": ["name", "on_event", "steps"]},
        "MixedBody": {
            "type": "object", "title": "MixedBody",
            "properties": {"note": {"type": "string"}}},
    }},
}


def _assembly(tools):
    doc = {"contract": "mcp.tools.v1", "domain": "flows", "source": "oss", "owner": "core/flows",
           "base_url_env": "FLOWS_API_URL", "served_at": "/.well-known/mcp-tools.json",
           "depends_on": ["identity"], "tools": tools}
    return m.assemble([doc], deployed={"identity", "flows"})


def test_a_bound_tool_takes_its_description_from_the_route():
    a = _assembly([{"name": "flows_list", "identity": "operator", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "GET", "path": "/flows"}}])
    bound = bind.verify(a, {"flows": FLOWS_OPENAPI})
    assert bound[0].description == "Every flow version"
    assert bound[0].name == "flows_list", "the MCP name is the manifest's, not the operationId's"


def test_a_route_the_domain_does_not_serve_fails_the_boot():
    """The manifest lying about its own service — the one failure this design could otherwise hide."""
    a = _assembly([{"name": "x", "identity": "user", "auth": "subject", "requires": ["identity", "flows"],
                    "route": {"method": "GET", "path": "/nope"}}])
    with pytest.raises(m.ManifestError, match="does not serve"):
        bind.verify(a, {"flows": FLOWS_OPENAPI})


def test_the_wrong_method_on_a_real_path_fails_too():
    a = _assembly([{"name": "x", "identity": "user", "auth": "subject", "requires": ["identity", "flows"],
                    "route": {"method": "DELETE", "path": "/flows"}}])
    with pytest.raises(m.ManifestError, match="does not serve"):
        bind.verify(a, {"flows": FLOWS_OPENAPI})


def test_an_argument_the_operation_does_not_take_fails_the_boot():
    """An argument an agent can pass and the route ignores is the worst reply available: the agent
    reports success on something that did not happen."""
    a = _assembly([{"name": "reactions_list", "identity": "operator", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "GET", "path": "/reactions"},
                    "arguments": ["status", "invented"]}])
    with pytest.raises(m.ManifestError, match="invented"):
        bind.verify(a, {"flows": FLOWS_OPENAPI})


def test_declared_arguments_carry_their_schema_from_the_operation():
    a = _assembly([{"name": "reactions_list", "identity": "operator", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "GET", "path": "/reactions"},
                    "arguments": ["status", "source_event_prefix"]}])
    bound = bind.verify(a, {"flows": FLOWS_OPENAPI})
    assert bound[0].parameters == {"status": {"type": "string"},
                                   "source_event_prefix": {"type": "string"}}


def test_a_path_parameter_is_an_argument_without_being_declared():
    """`/reactions/{reaction_id}/{verb}` cannot be called without them, so they are arguments
    whether or not the manifest lists them — and a manifest that had to restate them would be a
    second place to write the route."""
    a = _assembly([{"name": "reaction_signal", "identity": "user", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "POST", "path": "/reactions/{reaction_id}/{verb}"}}])
    bound = bind.verify(a, {"flows": FLOWS_OPENAPI})
    assert set(bound[0].path_params) == {"reaction_id", "verb"}


def test_an_edge_owned_tool_needs_no_openapi():
    doc = {"contract": "mcp.tools.v1", "domain": "gateway", "source": "oss",
           "owner": "core/gateway/services/mcp", "base_url_env": None, "served_at": None,
           "depends_on": ["identity"],
           "tools": [{"name": "vexa_overview", "identity": "none", "auth": "subject", "requires": ["identity"],
                      "route": None}]}
    a = m.assemble([doc], deployed={"identity"})
    assert bind.verify(a, {}) == []


def test_a_domain_with_no_openapi_at_all_fails_rather_than_binding_blind():
    a = _assembly([{"name": "flows_list", "identity": "operator", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "GET", "path": "/flows"}}])
    with pytest.raises(m.ManifestError, match="OpenAPI"):
        bind.verify(a, {})


# ── requestBody — an argument's schema derived from a route's JSON body, not just its query string ─

def test_a_json_body_tool_binds_against_the_owning_routes_requestbody():
    """`POST /flows` takes a `FlowSubmission` body ($ref-resolved against this domain's own
    `components.schemas`) — the same derive-not-restate mechanism `parameters` already had."""
    a = _assembly([{"name": "flows_submit", "identity": "operator", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "POST", "path": "/flows"},
                    "arguments": ["name", "on_event", "steps", "params", "activate"]}])
    bound = bind.verify(a, {"flows": FLOWS_OPENAPI})
    assert bound[0].parameters["name"] == {"type": "string", "description": "the flow's name"}
    assert bound[0].parameters["steps"] == {"type": "array", "items": {"type": "string"}}
    assert set(bound[0].body_params) == {"name", "on_event", "steps", "params", "activate"}
    assert bound[0].path_params == ()


def test_a_body_argument_the_operation_does_not_take_fails_the_boot_too():
    """The same "does not take" refusal `parameters` already enforces, now reachable through a
    `requestBody` schema — a name that is not one of `FlowSubmission`'s own fields."""
    a = _assembly([{"name": "flows_submit", "identity": "operator", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "POST", "path": "/flows"},
                    "arguments": ["name", "invented"]}])
    with pytest.raises(m.ManifestError, match="invented"):
        bind.verify(a, {"flows": FLOWS_OPENAPI})


def test_an_untyped_dict_body_has_nothing_to_derive():
    """`body: dict = Body(...)` publishes `{"type": "object", "additionalProperties": true}` with no
    named `properties` — exactly the shape `workspace_write`/`entity_upsert`/`propose` publish today.
    There is nothing here for a manifest to declare an argument against, so it fails the same
    "does not take" check an untyped query argument always has."""
    a = _assembly([{"name": "write_claims", "identity": "user", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "POST", "path": "/claims"},
                    "arguments": ["claims"]}])
    with pytest.raises(m.ManifestError, match="does not take"):
        bind.verify(a, {"flows": FLOWS_OPENAPI})


def test_a_route_with_no_declared_arguments_binds_even_with_an_untyped_body():
    """The untyped-dict shape only blocks a manifest that DECLARES an argument against it — a tool
    with none (the `reaction_signal` shape) binds fine regardless of what its body looks like."""
    a = _assembly([{"name": "write_claims", "identity": "user", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "POST", "path": "/claims"}}])
    bound = bind.verify(a, {"flows": FLOWS_OPENAPI})
    assert bound[0].parameters == {} and bound[0].body_params == ()


def test_query_and_body_arguments_on_the_same_route_are_told_apart():
    """A route can mix both halves of an operation — one query filter beside a JSON body — and
    `body_params` names exactly the subset that travels in the body."""
    a = _assembly([{"name": "mixed_tool", "identity": "user", "auth": "subject",
                    "requires": ["identity", "flows"],
                    "route": {"method": "POST", "path": "/mixed"},
                    "arguments": ["dry_run", "note"]}])
    bound = bind.verify(a, {"flows": FLOWS_OPENAPI})
    assert set(bound[0].parameters) == {"dry_run", "note"}
    assert bound[0].body_params == ("note",)


def test_a_name_in_both_query_and_body_refuses_rather_than_guesses():
    doc = {"contract": "mcp.tools.v1", "domain": "flows", "source": "oss", "owner": "core/flows",
           "base_url_env": "FLOWS_API_URL", "served_at": "/.well-known/mcp-tools.json",
           "depends_on": ["identity"],
           "tools": [{"name": "x", "identity": "user", "auth": "subject",
                      "requires": ["identity", "flows"],
                      "route": {"method": "POST", "path": "/collides"}}]}
    a = m.assemble([doc], deployed={"identity", "flows"})
    collide_openapi = {"paths": {"/collides": {"post": {"parameters": [
        {"name": "note", "in": "query", "schema": {"type": "string"}}],
        "requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "properties": {"note": {"type": "string"}}}}}}}}}}
    with pytest.raises(m.ManifestError, match="both its query parameters and its JSON body"):
        bind.verify(a, {"flows": collide_openapi})
