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
        "/flows": {"get": {"operationId": "list_flows", "summary": "Every flow version",
                           "parameters": []}},
        "/reactions": {"get": {"summary": "The operator projection", "parameters": [
            {"name": "status", "in": "query", "schema": {"type": "string"}},
            {"name": "source_event_prefix", "in": "query", "schema": {"type": "string"}}]}},
        "/reactions/{reaction_id}/{verb}": {"post": {"summary": "Steer one reaction",
                                                     "parameters": []}},
    }
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


# ── the description an agent actually reads (F-D12, F-D26) ──────────────────────────────────────
#
# FastAPI SYNTHESISES `summary` from the endpoint's function name when the route sets none, so
# every flows route publishes one whether or not anybody wrote it. Preferring it published tools
# whose whole description was their own title — `whats_waiting` read "Queue Waiting", and
# `report_friction` read "Report Friction" while its docstring, naming the eight `kind` words, sat
# one key away. On 2026-09-04 an agent guessed those words and twelve prod reports were refused.

def _bind_one(op, arguments=()):
    tool = {"name": "flows_list", "identity": "operator", "auth": "subject",
            "requires": ["identity", "flows"], "route": {"method": "GET", "path": "/flows"}}
    if arguments:
        tool["arguments"] = list(arguments)
    return bind.verify(_assembly([tool]), {"flows": {"paths": {"/flows": {"get": op}}}})[0]


def test_a_synthesised_title_does_not_beat_the_route_s_own_docstring():
    bt = _bind_one({"summary": "Report Friction", "parameters": [],
                    "description": "Tell us what did not work, and which `kind` word to use."})
    assert bt.description.endswith("which `kind` word to use.")
    assert bt.description != "Report Friction", "the tool is described by its own title"


def test_a_real_summary_is_kept_in_front_of_the_description_not_dropped():
    bt = _bind_one({"summary": "What is waiting for this person", "parameters": [],
                    "description": "The pending reactions, each naming the flow behind it."})
    assert bt.description.startswith("What is waiting for this person")
    assert "each naming the flow behind it" in bt.description


def test_a_summary_the_description_already_says_is_not_repeated():
    bt = _bind_one({"summary": "Tell us what did not work", "parameters": [],
                    "description": "Tell us what did not work, so a developer can fix it."})
    assert bt.description == "Tell us what did not work, so a developer can fix it."


def test_a_route_with_only_a_summary_is_still_described_by_it():
    assert _bind_one({"summary": "Every flow version",
                      "parameters": []}).description == "Every flow version"


def test_an_argument_s_allowed_values_survive_the_binding():
    """`kind` reaching an agent as a bare string with no allowed values is half of F-D26."""
    op = {"summary": "s", "description": "d", "parameters": [
        {"name": "kind", "in": "query", "description": "which kind",
         "schema": {"type": "string", "enum": ["error", "ux", "other"]}}]}
    bt = _bind_one(op, arguments=["kind"])
    assert bt.parameters["kind"]["enum"] == ["error", "ux", "other"]
    assert bt.parameters["kind"]["description"] == "which kind"


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
