"""agent-api's two broker callers go through one signer and one client.

- Each request carries a credential-broker.v1 assertion the contract's verifier accepts for exactly
  that method, path and body, signed with the caller's role key.
- The vendored signer reproduces the contract's golden vectors byte for byte.
- Every failure is a typed, logged fault — config, transport, http_<status>, parse — and the log
  line carries no key, body or response.
"""
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from control_plane import broker_assertion, broker_client, git_secret_store
from control_plane.routers import connections

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "credential-broker.v1"


@pytest.fixture
def keys(tmp_path, monkeypatch):
    agent = tmp_path / "agent.key"
    agent.write_text("fixture-agent-key-" + "a" * 40 + "\n")
    git = tmp_path / "git.key"
    git.write_text("fixture-git-key-" + "b" * 40 + "\n")
    monkeypatch.setenv("VEXA_CONNECTIONS_BROKER_URL", "http://credential-broker:8100")
    monkeypatch.setenv("VEXA_CONNECTIONS_AGENT_KEY_FILE", str(agent))
    monkeypatch.setenv("VEXA_GIT_STORE_BROKER_URL", "http://credential-broker:8100/")
    monkeypatch.setenv("VEXA_GIT_STORE_KEY_FILE", str(git))
    return {"agent": agent.read_bytes().strip(), "git": git.read_bytes().strip()}


@pytest.fixture
def broker(monkeypatch):
    """Route every broker_client request to a handler; record what was sent."""
    sent = []
    state = {"handler": lambda req: httpx.Response(200, json={"connections": []})}
    real = httpx.Client

    def client(**kwargs):
        def handle(req):
            sent.append(req)
            return state["handler"](req)
        return real(transport=httpx.MockTransport(handle), **{k: v for k, v in kwargs.items() if k != "transport"})
    monkeypatch.setattr(broker_client.httpx, "Client", client)
    state["sent"] = sent
    return state


def verified(req, key):
    path = req.url.raw_path.decode()
    return broker_assertion.verify(req.headers[broker_assertion.HEADER], key_for=lambda role: key,
                                   method=req.method, path=path, body=req.content)


def test_vendored_signer_is_the_contract_file():
    assert Path(broker_assertion.__file__).read_bytes() == (CONTRACT / "assertion.py").read_bytes()


@pytest.mark.parametrize("vector", sorted((CONTRACT / "golden").glob("SignedAssertionVector.*.json")), ids=lambda p: p.stem)
def test_vendored_signer_reproduces_golden_vectors(vector):
    v = json.loads(vector.read_text())
    c = v["claims"]
    assert broker_assertion.sign(v["key"].encode(), role=c["role"], actor=c["actor"], session=c["session"],
                                 method=c["method"], path=c["path"], body=v["request_body"].encode(),
                                 at=c["at"], nonce=c["nonce"]) == v["header"]


def test_connections_requests_are_signed_as_the_agent_for_the_exact_request(keys, broker):
    broker["handler"] = lambda req: httpx.Response(200, json={"connection_id": "a" * 32, "status": "awaiting_user"})
    connections.call_broker("u1", "POST", "/api/setup", {"provider": "google_email", "label": "Gmail"}, identity="signed-u1")
    req = broker["sent"][-1]
    assert str(req.url) == "http://credential-broker:8100/api/setup"
    claims = verified(req, keys["agent"])
    assert claims["role"] == "agent" and claims["actor"] == "u1"
    # the gateway's signature over u1 rides beside the assertion, unchanged (gateway-identity.v1)
    assert req.headers["x-vexa-identity"] == "signed-u1"
    with pytest.raises(broker_assertion.AssertionRefused):
        verified(req, keys["git"])          # the git key cannot have produced it


def test_git_store_signs_as_the_git_role_for_the_owner(keys, broker):
    broker["handler"] = lambda req: httpx.Response(200, json={"found": True, "value": "fixture"})
    assert git_secret_store._call("deploy/user-7.priv", "get") == {"found": True, "value": "fixture"}
    claims = verified(broker["sent"][-1], keys["git"])
    assert (claims["role"], claims["actor"]) == ("git", "user-7")
    assert "x-vexa-identity" not in broker["sent"][-1].headers
    assert json.loads(broker["sent"][-1].content) == {"name": "deploy/user-7.priv", "action": "get", "value": None}


def faults(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith('{"event":"broker_fault"')]


@pytest.mark.parametrize("handler,kind,status", [
    (lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")), "transport", 503),
    (lambda req: httpx.Response(500, text="PRIVATE-BODY"), "http_500", 503),
    (lambda req: httpx.Response(401, json={"detail": "Product identity refused"}), "http_401", 503),
    (lambda req: httpx.Response(200, text="not json"), "parse", 503),
])
def test_connection_faults_are_typed_and_logged_without_values(keys, broker, caplog, handler, kind, status):
    broker["handler"] = handler
    caplog.set_level(logging.WARNING)
    with pytest.raises(HTTPException) as exc:
        connections.call_broker("u1", "POST", "/api/connections/" + "c" * 32 + "/read", {"action": "gmail.search", "query": "PRIVATE-QUERY"}, identity="signed-u1")
    assert exc.value.status_code == status and exc.value.detail == "Connection service unavailable"
    logged = faults(caplog)
    assert logged and logged[-1]["kind"] == kind and logged[-1]["source"] == "credential-broker"
    assert logged[-1]["route"] == "/api/connections/{cid}/read" and logged[-1]["role"] == "agent"
    text = caplog.text
    assert "PRIVATE" not in text and "fixture-agent-key" not in text


def test_refusals_the_person_can_act_on_pass_through(keys, broker):
    broker["handler"] = lambda req: httpx.Response(409, json={"detail": "Matching connection is not ready"})
    with pytest.raises(HTTPException) as exc:
        connections.call_broker("u1", "GET", "/api/connections", identity="signed-u1")
    assert (exc.value.status_code, exc.value.detail) == (409, "Matching connection is not ready")


def test_unconfigured_is_its_own_answer(monkeypatch, broker, caplog):
    monkeypatch.delenv("VEXA_CONNECTIONS_BROKER_URL", raising=False)
    caplog.set_level(logging.WARNING)
    with pytest.raises(HTTPException) as exc:
        connections.call_broker("u1", "GET", "/api/connections", identity="signed-u1")
    assert exc.value.detail == "Connections are not configured on this deployment"
    assert faults(caplog)[-1]["kind"] == "config"
    assert not broker["sent"]


def test_short_key_never_signs(tmp_path, monkeypatch, broker, caplog):
    short = tmp_path / "short.key"
    short.write_text("too-short")
    monkeypatch.setenv("VEXA_CONNECTIONS_BROKER_URL", "http://credential-broker:8100")
    monkeypatch.setenv("VEXA_CONNECTIONS_AGENT_KEY_FILE", str(short))
    caplog.set_level(logging.WARNING)
    with pytest.raises(HTTPException):
        connections.call_broker("u1", "GET", "/api/connections", identity="signed-u1")
    assert faults(caplog)[-1]["kind"] == "config" and not broker["sent"]


@pytest.mark.parametrize("handler,kind", [
    (lambda req: httpx.Response(503, json={"detail": "Credential store unavailable"}), "http_503"),
    (lambda req: httpx.Response(200, json={"found": "yes"}), "parse"),
])
def test_git_store_faults_raise_unavailable_never_absent(keys, broker, caplog, handler, kind):
    broker["handler"] = handler
    caplog.set_level(logging.WARNING)
    with pytest.raises(git_secret_store.GitStoreUnavailable):
        git_secret_store._call("pat/2", "get")
    assert faults(caplog)[-1]["kind"] == kind and faults(caplog)[-1]["role"] == "git"
