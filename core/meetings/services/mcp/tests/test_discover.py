"""DISCOVERY — what a deployment carries, asked rather than assumed.

Eight configurations, not two: identity plus any subset of meetings, flows and agent. Which ones
exist is read off the base URLs the deployment sets, because a profile NAME would be a second place
to say what the URLs already say — and the configuration nobody named is the one that breaks.

No network: the domains are answered by an httpx MockTransport, which is this service's own idiom
for driving the shipped forwarding path offline.
"""
from __future__ import annotations

import json

import httpx
import pytest

from vexa_mcp import discover as d
from vexa_mcp import manifest as m

FLOWS_MANIFEST = {
    "contract": "mcp.tools.v1", "domain": "flows", "source": "oss", "owner": "core/flows",
    "base_url_env": "FLOWS_API_URL", "served_at": "/.well-known/mcp-tools.json",
    "depends_on": ["identity"],
    "tools": [{"name": "flows_list", "identity": "operator", "requires": ["identity", "flows"],
               "route": {"method": "GET", "path": "/flows"}}],
}
FLOWS_OPENAPI = {"paths": {"/flows": {"get": {"summary": "Every flow version", "parameters": []}}}}


def _transport(answers):
    def handler(request: httpx.Request) -> httpx.Response:
        body = answers.get(str(request.url))
        if body is None:
            return httpx.Response(404, json={"detail": "not here"})
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


def _answers(**extra):
    a = {"http://flows/.well-known/mcp-tools.json": FLOWS_MANIFEST,
         "http://flows/openapi.json": FLOWS_OPENAPI}
    a.update(extra)
    return a



def test_only_the_domains_the_deployment_names_are_asked():
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        d.discover(c, env={"ADMIN_API_URL": "http://identity"})
    assert all("identity" in u for u in asked), f"asked a domain nobody deployed: {asked}"



def test_a_deployed_domain_s_tools_are_assembled():
    with httpx.Client(transport=_transport(_answers())) as c:
        assembly, openapi, bases = d.discover(
            c, env={"ADMIN_API_URL": "http://identity", "FLOWS_API_URL": "http://flows"})
    assert [t.name for t in assembly.tools] == ["flows_list"]
    assert set(openapi) == {"flows"} and bases["flows"] == "http://flows"



def test_a_domain_with_no_manifest_yet_contributes_nothing_and_does_not_fail():
    """Not every domain has published one. That is a smaller surface, not a broken deployment."""
    with httpx.Client(transport=_transport(_answers())) as c:
        assembly, _o, _b = d.discover(
            c, env={"ADMIN_API_URL": "http://identity", "FLOWS_API_URL": "http://flows",
                    "MEETING_API_URL": "http://meetings"})
    assert [t.name for t in assembly.tools] == ["flows_list"]



def test_a_manifest_whose_openapi_does_not_answer_fails_the_boot():
    """Publishing a manifest is a promise about routes. Binding it blind would turn that promise
    into a tool that 404s the first time an agent calls it."""
    answers = _answers()
    answers.pop("http://flows/openapi.json")
    with httpx.Client(transport=_transport(answers)) as c:
        with pytest.raises(m.ManifestError, match="OpenAPI"):
            d.discover(c, env={"ADMIN_API_URL": "http://identity",
                                     "FLOWS_API_URL": "http://flows"})



def test_a_mounted_domain_with_no_url_fails_rather_than_listing_a_tool_it_cannot_reach(tmp_path):
    (tmp_path / "billing.mcp.tools.v1.json").write_text(json.dumps({
        "contract": "mcp.tools.v1", "domain": "billing", "source": "oss", "owner": "private",
        "base_url_env": "BILLING_API_URL", "served_at": "/.well-known/mcp-tools.json",
        "depends_on": ["identity"], "tools": []}))
    with httpx.Client(transport=_transport(_answers())) as c:
        with pytest.raises(m.ManifestError, match="BILLING_API_URL"):
            d.discover(c, env={"ADMIN_API_URL": "http://identity",
                                     "VEXA_MCP_MANIFEST_DIR": str(tmp_path)})



def test_an_empty_mount_is_the_oss_product_exactly(tmp_path):
    with httpx.Client(transport=_transport(_answers())) as c:
        assembly, _o, _b = d.discover(
            c, env={"ADMIN_API_URL": "http://identity", "FLOWS_API_URL": "http://flows",
                    "VEXA_MCP_MANIFEST_DIR": str(tmp_path)})
    assert [t.name for t in assembly.tools] == ["flows_list"]
    assert assembly.entitlement is None
