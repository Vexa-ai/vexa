"""gateway-identity.v1 at agent-api's door — who may name a person, one test per caller class.

agent-api reads WHO is calling from x-user-* headers. With the gateway's signing key configured
(every deployment: the production boot requires it), a header is believed only when:

  * the gateway signed it (X-Vexa-Identity)            — the terminal, a person's MCP client and a
                                                          cloud worker's toolbelt, all through the edge
  * the internal tier carries it (X-Internal-Secret)   — flows, the dogfood rig, agent-api's own
                                                          service calls
and a request that names nobody passes untouched to routes that need nobody (the runtime's
scheduler, the terminal's admin probes, health). Everything else is a 401 at the door.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import identity_token
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings

KEY = "agent-test-signing-key"
INTERNAL = "agent-test-internal-secret"


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_SECRET", KEY)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    return TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity())))


def _signed(sub: str, **claims) -> dict:
    return {identity_token.HEADER: identity_token.sign(KEY, {"sub": sub, **claims})}


# ── refused ─────────────────────────────────────────────────────────────────────────────────────
def test_an_unsigned_identity_is_refused(client):
    r = client.get("/api/chat/order", headers={"X-User-Id": "7"})
    assert r.status_code == 401
    assert "unsigned_identity" in r.json()["detail"]


@pytest.mark.parametrize("token", [
    identity_token.sign("someone-elses-key", {"sub": "7"}),
    identity_token.sign(KEY, {"sub": "7"}, now=1_000_000, ttl_sec=60),   # long expired
    "v1.not.a-token",
])
def test_a_forged_expired_or_malformed_signature_is_refused(client, token):
    r = client.get("/api/chat/order", headers={identity_token.HEADER: token, "X-User-Id": "7"})
    assert r.status_code == 401
    assert "invalid_identity" in r.json()["detail"]


def test_a_wrong_internal_secret_does_not_carry_an_identity(client):
    r = client.get("/api/chat/order", headers={"X-User-Id": "7", "X-Internal-Secret": "guess"})
    assert r.status_code == 401


def test_naming_nobody_is_401_and_there_is_no_fallback_subject(client, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_DEFAULT_SUBJECT", "u_live")
    c = TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity())))
    assert c.get("/api/chat/order").status_code == 401


# ── caller class: the gateway (terminal · MCP clients · a worker's toolbelt) ──────────────────────
def test_the_gateway_signed_identity_is_believed_and_wins_over_plain_headers(client):
    client.put("/api/chat/order", headers=_signed("7"), json={"order": ["s1"]})
    r = client.get("/api/chat/order", headers={**_signed("7"), "X-User-Id": "8"})
    assert r.status_code == 200 and r.json()["order"] == ["s1"]
    assert client.get("/api/chat/order", headers=_signed("8")).json()["order"] == []


def test_a_delegated_worker_without_a_person_is_refused_a_person_verb(client):
    worker = _signed("7", delegation={"regime": "autonomous", "workspaces": ["ws_1"]})
    r = client.post("/api/connections/request", headers=worker, json={"provider": "google_email"})
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "human_session_required"


def test_a_delegated_worker_is_held_to_its_workspace_ceiling(client):
    worker = _signed("7", delegation={"regime": "autonomous", "workspaces": ["ws_1"]})
    r = client.get("/api/workspace/tree", params={"slug": "ws_other"}, headers=worker)
    assert r.status_code == 403
    assert r.json()["detail"]["refused"] == "out_of_scope"


def test_a_human_regime_delegation_is_bounded_by_the_account_alone(client):
    worker = _signed("7", delegation={"regime": "human", "workspaces": "*"})
    r = client.get("/api/workspace/tree", params={"slug": "ws_other"}, headers=worker)
    detail = r.json().get("detail")
    assert not (isinstance(detail, dict) and detail.get("refused") == "out_of_scope")


# ── caller class: the internal tier (flows · the dogfood rig · agent-api's own service calls) ─────
def test_the_internal_tier_may_name_the_person_it_acts_for(client):
    r = client.get("/api/chat/order", headers={"X-User-Id": "7", "X-Internal-Secret": INTERNAL})
    assert r.status_code == 200


# ── caller class: nobody named (runtime scheduler · terminal admin probes · health) ───────────────
def test_a_request_that_names_nobody_reaches_routes_that_need_nobody(client):
    assert client.get("/health").status_code == 200
    # the runtime's scheduler fires routines here with no identity; the guard lets it through and
    # the route answers on its own terms (a schema refusal, never the guard's 401)
    r = client.post("/invocations", json={})
    assert r.status_code != 401
