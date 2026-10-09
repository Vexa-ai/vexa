"""The `/internal/validate` wire: a named request body and one named response model.

The gateway, flows and the terminal's server all read this answer, and the gateway signs it into the
identity every service behind it trusts. Its shape is declared once (`app/validate.py`) and pinned
here: which fields exist, which ones appear for which bearer, and that a field which does not apply is
OMITTED rather than sent as null — the shape every caller has always read.

No database: `get_db` is overridden with a session that knows one API key and its account.
"""
from datetime import datetime, timedelta
from typing import get_type_hints

import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import APIToken, User

INTERNAL = "internal-secret-for-tests"
DLG = "delegation-secret-for-tests"
KEY = "vxa_bot_test-key"

BASE_FIELDS = {"user_id", "scopes", "max_concurrent", "email", "is_admin"}


class _Result:
    def __init__(self, row=None, user=None):
        self._row, self._user = row, user

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._user


class _Session:
    """Answers the two lookups the oracle makes: a token row joined to its user (by the key string)
    and a user by id (for a delegation's subject)."""

    def __init__(self, tokens, users):
        self._tokens, self._users = tokens, users
        self.commits = 0

    async def execute(self, stmt):
        params = list(stmt.compile().params.values())
        key = next((v for v in params if isinstance(v, str)), None)
        if key is not None:
            return _Result(row=self._tokens.get(key))
        uid = next((v for v in params if isinstance(v, int)), None)
        return _Result(user=self._users.get(uid))

    async def commit(self):
        self.commits += 1


def _client(monkeypatch, *, data=None, scopes=("bot",), expires_at=None):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    user = User(id=42, email="ada@example.com", max_concurrent_bots=2, data=data or {})
    tok = APIToken(token=KEY, user_id=42, scopes=list(scopes) if scopes is not None else None,
                   expires_at=expires_at)
    session = _Session({KEY: (tok, user)}, {42: user})
    app = create_app()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app), session


def _validate(client, body):
    return client.post("/internal/validate", json=body, headers={"X-Internal-Secret": INTERNAL})


def test_the_response_model_declares_the_whole_wire_in_one_place():
    assert set(validate_mod.ValidatedIdentity.model_fields) == BASE_FIELDS | {
        "webhook_url", "webhook_secret", "webhook_events", "workspaces", "delegation",
        "person_is_admin"}
    assert set(validate_mod.DelegationCeiling.model_fields) == {"regime", "workspaces", "target"}
    assert set(validate_mod.ValidateRequest.model_fields) == {"token"}


def test_the_route_serves_the_named_models():
    route = next(r for r in validate_mod.router.routes if r.path == "/internal/validate")
    assert route.response_model is validate_mod.ValidatedIdentity
    assert route.response_model_exclude_unset is True
    assert get_type_hints(route.endpoint)["payload"] is validate_mod.ValidateRequest


def test_an_api_key_answers_the_base_fields_and_nothing_that_does_not_apply(monkeypatch):
    client, session = _client(monkeypatch)
    r = _validate(client, {"token": KEY})
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": 42, "scopes": ["bot"], "max_concurrent": 2,
                        "email": "ada@example.com", "is_admin": False}
    assert session.commits == 1, "every hit bumps last_used_at"


def test_webhook_and_memberships_appear_only_when_the_account_has_them(monkeypatch):
    client, _ = _client(monkeypatch, data={
        "webhook_url": "https://hooks.example.com/x", "webhook_secret": "s3cret-value",
        "webhook_events": {"meeting.completed": True},
        "memberships": [{"workspace_id": "ws_1"}, {"role": "viewer"}, "junk"],
        "is_admin": True})
    body = _validate(client, {"token": KEY}).json()
    assert set(body) == BASE_FIELDS | {"webhook_url", "webhook_secret", "webhook_events", "workspaces"}
    assert body["webhook_events"] == {"meeting.completed": True}
    assert body["workspaces"] == ["ws_1"]
    assert body["is_admin"] is True


def test_a_webhook_secret_without_a_url_is_not_disclosed(monkeypatch):
    client, _ = _client(monkeypatch, data={"webhook_secret": "orphan-secret"})
    assert set(_validate(client, {"token": KEY}).json()) == BASE_FIELDS


def test_an_unscoped_key_reads_as_legacy(monkeypatch):
    client, _ = _client(monkeypatch, scopes=None)
    assert _validate(client, {"token": KEY}).json()["scopes"] == ["legacy"]


def test_an_expired_key_is_401(monkeypatch):
    client, _ = _client(monkeypatch, expires_at=datetime.utcnow() - timedelta(seconds=5))
    r = _validate(client, {"token": KEY})
    assert r.status_code == 401 and r.json()["detail"] == "Token expired"


def test_an_unknown_key_is_401(monkeypatch):
    client, _ = _client(monkeypatch)
    assert _validate(client, {"token": "vxa_bot_nobody"}).status_code == 401


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": None}])
def test_a_missing_token_is_401_not_422(monkeypatch, body):
    client, _ = _client(monkeypatch)
    r = _validate(client, body)
    assert r.status_code == 401 and r.json()["detail"] == "Missing token"


@pytest.mark.parametrize("body", [{"token": KEY, "scopes": ["browser"]}, ["not", "an", "object"],
                                  {"token": 42}])
def test_a_body_that_is_not_the_request_shape_is_refused(monkeypatch, body):
    client, _ = _client(monkeypatch)
    assert _validate(client, body).status_code == 422


def test_a_delegation_answers_the_ceiling_and_the_person_and_omits_an_absent_target(monkeypatch):
    client, _ = _client(monkeypatch, data={"is_admin": True})
    tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces="*")
    body = _validate(client, {"token": tok}).json()
    assert set(body) == BASE_FIELDS | {"delegation", "person_is_admin"}
    assert body["delegation"] == {"regime": "human", "workspaces": "*"}
    assert body["scopes"] == ["bot", "tx"]
    assert body["is_admin"] is False and body["person_is_admin"] is True


def test_the_internal_tier_guards_the_typed_route(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.post("/internal/validate", json={"token": KEY}).status_code == 403
    assert client.post("/internal/validate", json={"token": KEY},
                       headers={"X-Internal-Secret": "wrong"}).status_code == 403
