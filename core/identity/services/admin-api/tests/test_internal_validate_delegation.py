"""A worker's delegation token resolves through the same oracle as an API key.

`/internal/validate` is identity's answer to "who is this bearer" for every resolver (the gateway,
flows). A `vxd_` delegation token minted by agent-api for one dispatch is a bearer too: verified
here (signature, audience, expiry), bound to an account that still exists, answered with the
person's identity, the two service scopes, and the dispatch's ceiling as `delegation`.

No database: `get_db` is overridden with a session that knows one user, so this runs anywhere.
"""
import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

INTERNAL = "internal-secret-for-tests"
DLG = "delegation-secret-for-tests"


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user

    def first(self):
        return None


class _Session:
    def __init__(self, users):
        self._users = users

    async def execute(self, stmt):
        params = stmt.compile().params
        uid = next((v for v in params.values() if isinstance(v, int)), None)
        return _Result(self._users.get(uid))

    async def commit(self):
        pass


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    user = User(id=42, email="ada@example.com", max_concurrent_bots=2,
                data={"is_admin": True, "memberships": [{"workspace_id": "ws_1"}]})
    app = create_app()

    async def _db():
        yield _Session({42: user})

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _validate(client, token):
    return client.post("/internal/validate", json={"token": token},
                       headers={"X-Internal-Secret": INTERNAL})


def test_a_delegation_token_resolves_to_its_person_and_ceiling(client):
    tok = delegation.mint_delegation(DLG, subject="42", regime="autonomous", workspaces=["ws_1"],
                                     target="ws_1")
    r = _validate(client, tok)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == 42
    assert body["email"] == "ada@example.com"
    assert body["max_concurrent"] == 2
    assert body["workspaces"] == ["ws_1"]
    assert sorted(body["scopes"]) == ["bot", "tx"]
    assert body["is_admin"] is False, "a worker never carries its person's admin role"
    assert body["delegation"] == {"regime": "autonomous", "workspaces": ["ws_1"], "target": "ws_1"}


def test_a_human_regime_token_carries_the_soft_ceiling(client):
    tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces="*")
    assert _validate(client, tok).json()["delegation"] == {"regime": "human", "workspaces": "*"}


@pytest.mark.parametrize("token", [
    delegation.mint_delegation("another-key", subject="42"),
    delegation.mint_delegation(DLG, subject="42", ttl_sec=-10),
    "vxd_not.a.token",
])
def test_a_forged_expired_or_malformed_token_is_401(client, token):
    assert _validate(client, token).status_code == 401


def test_a_token_for_an_account_that_no_longer_exists_is_401(client):
    tok = delegation.mint_delegation(DLG, subject="999")
    r = _validate(client, tok)
    assert r.status_code == 401
    assert "no such user" in r.json()["detail"]


def test_a_deployment_without_the_delegation_key_refuses_every_delegation(client, monkeypatch):
    monkeypatch.delenv("VEXA_MCP_DELEGATION_SECRET")
    tok = delegation.mint_delegation(DLG, subject="42")
    assert _validate(client, tok).status_code == 401


def test_the_internal_tier_still_guards_the_oracle(client):
    tok = delegation.mint_delegation(DLG, subject="42")
    assert client.post("/internal/validate", json={"token": tok}).status_code == 403
