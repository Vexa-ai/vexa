"""THE ASSEMBLER — one MCP server, built from the manifests of the domains that are deployed.

PRD decision 40, founder rulings of 2026-09-02. A tool belongs to the domain that owns the door
behind it; this service is an EDGE that assembles what those domains declare, and holds no domain
logic of its own. It also reads a MOUNTED manifest directory, so a private paid domain composes onto
the line without a line of it living in this repo.

Every rule below fails the BOOT rather than degrading, and each one is a failure that would
otherwise be silent:

  * a domain that is deployed but does not answer — "the meetings tools are missing" must never be
    something a person discovers by asking for one;
  * a tool name claimed twice — last-one-wins would let a mounted private manifest shadow an OSS
    tool, which is the one thing a mount must never be able to do;
  * a manifest naming a route its own service does not serve — a manifest lying about its service is
    the only failure this design could otherwise hide;
  * two entitlement hooks — "who decides whether this person may act" is not a question with two
    answers.

A tool whose `requires` is not satisfied is ABSENT from tools/list, never present-and-failing: an
agent that cannot see a tool recovers, one told a tool exists and handed a 502 tells the person the
product is broken.
"""
from __future__ import annotations

import json

import pytest

from vexa_mcp import manifest as m


def _manifest(domain="flows", tools=None, **kw):
    doc = {
        "contract": "mcp.tools.v1", "domain": domain, "source": kw.pop("source", "oss"),
        "owner": f"core/{domain}", "base_url_env": f"{domain.upper()}_API_URL",
        "served_at": "/.well-known/mcp-tools.json", "depends_on": ["identity"],
        "tools": tools if tools is not None else [
            {"name": "flows_list", "identity": "operator", "requires": ["identity", "flows"],
             "route": {"method": "GET", "path": "/flows"}}],
    }
    doc.update(kw)
    return doc


# ── what a manifest must be ──────────────────────────────────────────────────────────────────
def test_a_manifest_may_only_depend_on_identity():
    """The independence ruling, checked where it is declared. Meetings, flows and agent each point
    at identity and nothing else, which is what makes every configuration a product."""
    with pytest.raises(m.ManifestError, match="depends_on"):
        m.validate(_manifest(depends_on=["identity", "agent"]))
    m.validate(_manifest(depends_on=["identity"]))
    m.validate(_manifest(domain="identity", depends_on=[], base_url_env="ADMIN_API_URL", tools=[
        {"name": "settings", "identity": "user", "requires": ["identity"],
         "route": {"method": "GET", "path": "/user/settings"}}]))


def test_a_tool_requires_its_own_domain_and_identity_only():
    with pytest.raises(m.ManifestError, match="requires"):
        m.validate(_manifest(tools=[
            {"name": "x", "identity": "user", "requires": ["identity", "meetings"],
             "route": {"method": "GET", "path": "/x"}}]))


def test_an_edge_owned_tool_may_have_no_route_only_when_it_has_no_door():
    m.validate(_manifest(domain="gateway", base_url_env=None, served_at=None, tools=[
        {"name": "vexa_overview", "identity": "none", "requires": ["identity"], "route": None}]))
    with pytest.raises(m.ManifestError, match="route"):
        m.validate(_manifest(tools=[
            {"name": "x", "identity": "user", "requires": ["identity", "flows"], "route": None}]))


# ── assembly ─────────────────────────────────────────────────────────────────────────────────
def test_only_tools_whose_domains_are_deployed_are_served():
    """Eight configurations, not two. A tool the deployment cannot satisfy is ABSENT."""
    flows = _manifest()
    agent = _manifest(domain="agent", base_url_env="AGENT_API_URL", tools=[
        {"name": "workspace_tree", "identity": "user", "requires": ["identity", "agent"],
         "route": {"method": "GET", "path": "/api/workspace/tree"}}])
    both = m.assemble([flows, agent], deployed={"identity", "flows", "agent"})
    assert {t.name for t in both.tools} == {"flows_list", "workspace_tree"}
    no_agents = m.assemble([flows, agent], deployed={"identity", "flows"})
    assert {t.name for t in no_agents.tools} == {"flows_list"}
    assert "agent" in no_agents.absent_domains


def test_a_name_claimed_twice_fails_the_boot_and_names_both():
    a = _manifest()
    b = _manifest(domain="agent", base_url_env="AGENT_API_URL", tools=[
        {"name": "flows_list", "identity": "user", "requires": ["identity", "agent"],
         "route": {"method": "GET", "path": "/x"}}])
    with pytest.raises(m.ManifestError) as e:
        m.assemble([a, b], deployed={"identity", "flows", "agent"})
    assert "flows_list" in str(e.value) and "flows" in str(e.value) and "agent" in str(e.value)


def test_a_mounted_manifest_gets_no_precedence_over_an_oss_one():
    """The rule that makes a private mount safe: it collides, it does not shadow."""
    oss = _manifest()
    mounted = _manifest(domain="billing", source="mounted", base_url_env="BILLING_API_URL",
                        tools=[{"name": "flows_list", "identity": "user",
                                "requires": ["identity", "billing"],
                                "route": {"method": "GET", "path": "/seats"}}])
    with pytest.raises(m.ManifestError, match="claimed"):
        m.assemble([oss, mounted], deployed={"identity", "flows", "billing"})


def test_at_most_one_manifest_may_declare_the_entitlement_hook():
    ent = {"route": {"method": "GET", "path": "/entitlement"}, "answers": "entitled(subject)"}
    one = m.assemble([_manifest(domain="billing", source="mounted",
                                base_url_env="BILLING_API_URL", entitlement=ent, tools=[])],
                     deployed={"identity", "billing"})
    assert one.entitlement is not None and one.entitlement.domain == "billing"
    with pytest.raises(m.ManifestError, match="entitlement"):
        m.assemble([_manifest(domain="billing", source="mounted",
                              base_url_env="BILLING_API_URL", entitlement=ent, tools=[]),
                    _manifest(domain="flows", entitlement=ent)],
                   deployed={"identity", "billing", "flows"})


def test_no_entitlement_hook_means_everyone_is_entitled():
    """No manifest declares it => there is no hook => the OSS product exactly. This repo never
    carries a gate shipped dark."""
    assembled = m.assemble([_manifest()], deployed={"identity", "flows"})
    assert assembled.entitlement is None


def test_a_deployed_domain_that_does_not_answer_fails_the_boot():
    """"The meetings tools are missing" must never be something a person finds by asking."""
    with pytest.raises(m.ManifestError, match="did not answer"):
        m.assemble([_manifest()], deployed={"identity", "flows", "meetings"},
                   required_domains={"meetings"})


# ── the mounted directory ────────────────────────────────────────────────────────────────────
def test_the_mounted_directory_is_empty_by_default_and_that_is_the_oss_product(tmp_path):
    assert m.load_mounted(None) == []
    assert m.load_mounted(str(tmp_path)) == []


def test_a_mounted_file_is_read_and_marked_mounted(tmp_path):
    doc = _manifest(domain="billing", source="oss", base_url_env="BILLING_API_URL", tools=[])
    (tmp_path / "billing.mcp.tools.v1.json").write_text(json.dumps(doc))
    loaded = m.load_mounted(str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0]["source"] == "mounted", "a file in the mount is mounted whatever it claims"


def test_a_malformed_mounted_file_fails_the_boot_rather_than_being_skipped(tmp_path):
    """Skipping it would mean a paid deployment silently missing the tools it paid for."""
    (tmp_path / "broken.mcp.tools.v1.json").write_text("{not json")
    with pytest.raises(m.ManifestError, match="broken"):
        m.load_mounted(str(tmp_path))


# ── one authentication path (PRD 40.8) ───────────────────────────────────────────────────────
def test_a_tool_may_not_take_a_credential_as_an_argument():
    """The rig's 64 tools each carried `token=""` — a credential in an argument list, which is in
    the transcript forever and cannot be withdrawn. The edge has ONE path: a bearer header, with the
    session bound by Mcp-Session-Id. It is enforced here because this is where a tool's surface is
    decided; a route's OpenAPI has no such parameter, so the rule is mostly kept by construction and
    this catches a domain that reintroduces one on purpose."""
    for arg in ("token", "api_key", "access_token", "password"):
        with pytest.raises(m.ManifestError, match="one authentication path"):
            m.validate(_manifest(tools=[
                {"name": "x", "identity": "user", "requires": ["identity", "flows"],
                 "route": {"method": "GET", "path": "/x"}, "arguments": [arg, "since"]}]))


def test_ordinary_arguments_are_untouched():
    m.validate(_manifest(tools=[
        {"name": "x", "identity": "user", "requires": ["identity", "flows"],
         "route": {"method": "GET", "path": "/x"}, "arguments": ["since", "limit", "status"]}]))
