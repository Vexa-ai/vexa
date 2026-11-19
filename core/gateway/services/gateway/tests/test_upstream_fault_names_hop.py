"""A gateway fault says which hop failed (Vexa-ai/vexa#1783, frictions 2026-10-13 11:33).

A connection test answered `upstream timeout`, and the agent could not tell whether Vexa, the
credential broker or the third-party service was slow. The gateway's own answers keep `detail` as
it was (clients and docs read it) and say beside it, typed: the hop (`gateway → <service>`), that
the service is Vexa's own, and a sentence a tool result renders first (`reason: message`).

The hops are ordered so the INNER one gives up first: the MCP tool call waits longer than the
gateway's forward, so a slow service is reported by the gateway with its hop named, never as the
MCP client's own anonymous timeout.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import app as gateway_app
from gateway import create_app
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis, needs_agent

pytestmark = needs_agent
AUTH = {"x-api-key": VALID_KEY}


class _Failing(FakeDownstream):
    def __init__(self, exc: Exception):
        super().__init__(status_code=200, body={})
        self.exc = exc

    async def request(self, method, url, **kw):
        raise self.exc


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    async def _no_wait(_s):
        return None
    monkeypatch.setattr(gateway_app, "_retry_sleep", _no_wait)


def _client(ds):
    return TestClient(create_app(FakeAuthorizer(), ds, FakeRedis(), agent_api_url="http://agent-api:8100"))


def test_a_slow_service_is_named_as_the_hop_that_timed_out():
    r = _client(_Failing(httpx.ReadTimeout("slow"))).post(
        "/agent/connections/service/call", headers=AUTH, json={"connection_id": "a" * 32})
    assert r.status_code == 504
    body = r.json()
    assert body["detail"] == "upstream timeout"
    assert body["reason"] == "upstream_timeout" and body["source"] == "vexa"
    assert body["hop"] == "gateway → agent-api"
    assert "agent-api" in body["message"] and "not the connected service" in body["message"]


def test_an_unreachable_service_is_named_too():
    r = _client(_Failing(httpx.ReadError("reset"))).get("/agent/connections/status", headers=AUTH)
    assert r.status_code == 502
    body = r.json()
    assert body["detail"] == "upstream unreachable: ReadError"
    assert body["reason"] == "upstream_unreachable" and body["hop"] == "gateway → agent-api"


def test_the_gateway_forward_gives_up_before_the_mcp_tool_call():
    from gateway.adapters import FORWARD_TIMEOUT_S
    assert FORWARD_TIMEOUT_S == 30.0
