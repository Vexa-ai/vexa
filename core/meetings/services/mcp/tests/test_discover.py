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
    "tools": [{"name": "flows_list", "identity": "operator", "auth": "subject", "requires": ["identity", "flows"],
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


#: The boot waits out a cold-start race (5 attempts, 2s apart). The suite exercises the retry path
#: and not the wall clock, so every env below sets the pause to zero.
NO_WAIT = {"VEXA_MCP_BOOT_PROBE_PAUSE_SECONDS": "0"}


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)



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
    """Not every domain has published one. That is a smaller surface, not a broken deployment.

    NOTE THE CONTRAST with the three tests below: a 404 is an ANSWER — the domain is up and says it
    serves no manifest. Silence is a different event and fails the boot."""
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



# ── the fail direction this module's docstring promises ──────────────────────────────────────
#
# "A domain that IS configured and does not answer FAILS THE BOOT" was written in two places and
# true in neither: every exception was swallowed, so a configured domain whose port was refusing
# lost ALL of its tools, permanently and silently, and the server answered `tools/list` with a
# short list nobody could tell was short. On a cold `compose up` that is a coin flip on whether
# `whats_waiting` — the tool the server instructions name as every session's first call — exists.

def test_a_configured_domain_that_never_answers_fails_the_boot_and_names_it():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("http://flows"):
            return _refused(request)
        return httpx.Response(404, json={"detail": "not here"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(m.ManifestError) as e:
            d.discover(c, env={"ADMIN_API_URL": "http://identity",
                               "FLOWS_API_URL": "http://flows", **NO_WAIT})
    assert "flows" in str(e.value), "the boot message has to name the domain that went missing"
    assert "http://flows" in str(e.value), "and where it was looked for"


def test_a_5xx_is_silence_too_because_the_domain_did_not_author_it():
    """A gateway in front of a service that has not started answers 502. That is the door, not the
    domain — the same event as a refused connection, and it must not be read as "no manifest"."""
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("http://flows"):
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(404, json={"detail": "not here"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(m.ManifestError, match="flows"):
            d.discover(c, env={"ADMIN_API_URL": "http://identity",
                               "FLOWS_API_URL": "http://flows", **NO_WAIT})


def test_a_domain_that_is_slow_to_come_up_is_waited_for_rather_than_dropped():
    """The cold-start race, which is why the refusal is BOUNDED RETRY and not first-attempt. Two
    refused connections then a live domain must produce the same surface as a domain that was up."""
    answers, refusals = _answers(), {"left": 2}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("http://flows") and refusals["left"]:
            refusals["left"] -= 1
            return _refused(request)
        body = answers.get(url)
        if body is None:
            return httpx.Response(404, json={"detail": "not here"})
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assembly, openapi, _b = d.discover(
            c, env={"ADMIN_API_URL": "http://identity", "FLOWS_API_URL": "http://flows", **NO_WAIT})
    assert refusals["left"] == 0, "the retries were not actually spent"
    assert [t.name for t in assembly.tools] == ["flows_list"]
    assert set(openapi) == {"flows"}


def test_an_unconfigured_domain_is_never_asked_and_never_fails_the_boot():
    """The opposite fail direction, and the reason the rule above is safe: a domain nobody named
    contributes nothing, is not asked, and absent is a state an agent recovers from."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "flows" not in str(request.url), "asked a domain nobody configured"
        return httpx.Response(404, json={"detail": "not here"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assembly, openapi, bases = d.discover(
            c, env={"ADMIN_API_URL": "http://identity", **NO_WAIT})
    assert assembly.tools == [] and openapi == {} and set(bases) == {"identity"}


def test_the_wait_is_bounded_by_the_operator_s_attempt_count():
    tries = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        tries["n"] += 1
        return _refused(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(m.ManifestError):
            d.discover(c, env={"ADMIN_API_URL": "http://identity",
                               "VEXA_MCP_BOOT_PROBE_ATTEMPTS": "3", **NO_WAIT})
    assert tries["n"] == 3, "boot must not retry forever behind a domain that is simply gone"


def test_a_domain_that_published_a_manifest_and_then_went_silent_fails_the_boot():
    """The OpenAPI hop takes the same rule: a manifest is a promise about routes, and binding a
    tool without the spec that describes it is binding it blind."""
    answers = _answers()
    answers.pop("http://flows/openapi.json")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/openapi.json"):
            return _refused(request)
        body = answers.get(url)
        if body is None:
            return httpx.Response(404, json={"detail": "not here"})
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(m.ManifestError, match="OpenAPI"):
            d.discover(c, env={"ADMIN_API_URL": "http://identity",
                               "FLOWS_API_URL": "http://flows", **NO_WAIT})


def test_an_empty_mount_is_the_oss_product_exactly(tmp_path):
    with httpx.Client(transport=_transport(_answers())) as c:
        assembly, _o, _b = d.discover(
            c, env={"ADMIN_API_URL": "http://identity", "FLOWS_API_URL": "http://flows",
                    "VEXA_MCP_MANIFEST_DIR": str(tmp_path)})
    assert [t.name for t in assembly.tools] == ["flows_list"]
    assert assembly.entitlement is None
