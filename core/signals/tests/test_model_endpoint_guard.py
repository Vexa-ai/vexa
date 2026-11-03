"""The signal model's ``base_url`` comes from ``_global/POLICIES.md`` — written by the instance admin and
by the admin's own chat agent — and the call carries the meeting transcript and a broker-held key. It is
a URL somebody else supplied, so it is held to outbound-url.v1: resolved, every address checked, the
connection pinned, internal hosts only by the operator's ``VEXA_SIGNALS_MODEL_ALLOW`` (env, never policy).

DNS is replaced at ``socket.getaddrinfo`` so these read the same on a tree with no guard, where every
call below went straight through to the endpoint.
"""
from __future__ import annotations

import json
import socket

import httpx
import pytest

from meeting_signals import model
from meeting_signals.model import Line, ModelRefused, OpenAICompatibleAdapter

ALLOW_ENV = "VEXA_SIGNALS_MODEL_ALLOW"
PUBLIC = "93.184.215.14"
ASKS = {"decision": "Was a decision made?"}
NEW = Line("s1", "Ann", "we decided", 0.0, 1.0)


class Dns:
    def __init__(self):
        self.answers: dict[str, list[list[str]]] = {}

    def __call__(self, host, *args, **kwargs):
        script = self.answers.get(host)
        if not script:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        ips = script.pop(0) if len(script) > 1 else script[0]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
                for ip in ips]


@pytest.fixture
def dns(monkeypatch):
    table = Dns()
    monkeypatch.setattr(socket, "getaddrinfo", table)
    if hasattr(model, "resolver"):
        monkeypatch.setattr(model, "resolver", None)        # the real resolver, over the table above
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    return table


@pytest.fixture
def endpoint():
    """A model endpoint that answers, and records every request that reached it."""
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"decision": 0.9})}}],
                                          "usage": {"total_tokens": 10}})
    return seen, httpx.MockTransport(handler)


def score(base_url, key, transport):
    import asyncio
    return asyncio.run(OpenAICompatibleAdapter("m", key, base_url=base_url, transport=transport)
                       .score(ASKS, NEW, []))


@pytest.mark.parametrize("answer", [["10.0.0.7"], ["169.254.169.254"], ["127.0.0.1"], ["100.64.0.9"],
                                    ["fd00::1"], [PUBLIC, "192.168.0.4"]])
def test_an_admin_endpoint_that_resolves_inside_is_refused(answer, dns, endpoint):
    seen, transport = endpoint
    dns.answers["llm.corp.example"] = [answer]
    with pytest.raises(ModelRefused, match="endpoint refused"):
        score("https://llm.corp.example/v1", "sk-broker", transport)
    assert seen == []                     # no transcript and no key left the process


@pytest.mark.parametrize("base", ["http://admin-api:8001/v1", "http://169.254.169.254/latest",
                                  "http://10.0.0.7:4000/v1", "http://localhost:4000/v1"])
def test_literal_and_deployment_names_are_refused(base, dns, endpoint):
    seen, transport = endpoint
    with pytest.raises(ModelRefused):
        score(base, None, transport)
    assert seen == []


def test_a_public_endpoint_is_reached_at_the_checked_address(dns, endpoint):
    seen, transport = endpoint
    dns.answers["llm.public.example"] = [[PUBLIC]]
    out = score("https://llm.public.example/v1", "sk-broker", transport)
    assert out.scores == {"decision": 0.9}
    assert seen[0].url.host == PUBLIC and seen[0].headers["host"] == "llm.public.example"


def test_the_operator_names_an_internal_model_server(dns, endpoint, monkeypatch):
    """OeNB's case: LiteLLM/vLLM inside the bank, named by the operator — by host or by network."""
    seen, transport = endpoint
    dns.answers["llm.corp.example"] = [["10.0.0.7"]]
    dns.answers["other.corp.example"] = [["10.0.0.8"]]
    monkeypatch.setenv(ALLOW_ENV, "llm.corp.example")
    assert score("http://llm.corp.example:4000/v1", "sk-broker", transport).scores == {"decision": 0.9}
    assert seen[-1].url.host == "10.0.0.7"
    with pytest.raises(ModelRefused):
        score("https://other.corp.example/v1", None, transport)
    monkeypatch.setenv(ALLOW_ENV, "10.0.0.0/24")
    assert score("https://other.corp.example/v1", None, transport).scores == {"decision": 0.9}


def test_the_allowance_never_reaches_metadata(dns, endpoint, monkeypatch):
    seen, transport = endpoint
    monkeypatch.setenv(ALLOW_ENV, "llm.corp.example")
    dns.answers["llm.corp.example"] = [["169.254.169.254"]]
    with pytest.raises(ModelRefused):
        score("https://llm.corp.example/v1", None, transport)
    assert seen == []


def test_a_rebound_record_is_refused_on_the_next_request(dns, endpoint):
    seen, transport = endpoint
    dns.answers["flip.example"] = [[PUBLIC], ["169.254.169.254"]]
    score("https://flip.example/v1", "sk-broker", transport)
    with pytest.raises(ModelRefused):
        score("https://flip.example/v1", "sk-broker", transport)
    assert len(seen) == 1


def test_a_key_never_rides_cleartext_to_a_host_the_operator_did_not_name(dns, endpoint):
    seen, transport = endpoint
    dns.answers["llm.public.example"] = [[PUBLIC]]
    with pytest.raises(ModelRefused, match="http://"):
        score("http://llm.public.example/v1", "sk-broker", transport)
    assert seen == []
    assert score("http://llm.public.example/v1", None, transport).scores == {"decision": 0.9}


def test_a_malformed_allowance_admits_nothing(dns, endpoint, monkeypatch):
    seen, transport = endpoint
    monkeypatch.setenv(ALLOW_ENV, "*.corp.example")
    dns.answers["llm.corp.example"] = [["10.0.0.7"]]
    with pytest.raises(ModelRefused):
        score("https://llm.corp.example/v1", None, transport)
    assert seen == []


def test_the_built_in_endpoint_is_deployment_code(dns, endpoint):
    """No ``base_url``: the adapter's own default (OpenRouter) is not an admin-supplied URL."""
    seen, transport = endpoint
    assert score("", "sk", transport).scores == {"decision": 0.9}
    assert seen[0].url.host == "openrouter.ai"


def test_a_malformed_allowance_refuses_the_boot():
    from meeting_signals.__main__ import boot_model_allowance
    from meeting_signals.config_preflight import ConfigError
    with pytest.raises(ConfigError, match=ALLOW_ENV):
        boot_model_allowance({ALLOW_ENV: "169.254.0.0/16"})
    boot_model_allowance({ALLOW_ENV: "llm.corp.example, 10.20.0.0/16"})
    boot_model_allowance({})
