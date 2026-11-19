"""`/internal/validate` states `email_verified` (identity.v1): true only when a sign-in proved the
account's CURRENT address (`public_profile.record_email_verified`). An account made by an API call,
or one whose address changed since it was proven, answers false — so nothing keyed by the address
alone (a CRM Person claim) is granted on an address merely asserted."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from admin_api.app import public_profile as pp
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

INTERNAL = "internal-secret-for-tests"
DLG = "delegation-secret-for-tests"
ADDR = "ada@example.com"


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def first(self):
        return None


class _Session:
    def __init__(self, user):
        self.user = user

    async def execute(self, stmt):
        return _Result(self.user)

    async def commit(self):
        pass


def _client(monkeypatch, user):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    app = create_app()

    async def _db():
        yield _Session(user)

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _delegation_token() -> str:
    now = int(time.time())
    canon = lambda d: json.dumps(d, separators=(",", ":"), sort_keys=True).encode()  # noqa: E731
    body = _b64u(canon({"alg": "HS256", "typ": "vxdlg"})) + "." + _b64u(canon(
        {"sub": "42", "aud": "vexa-mcp", "scope": {"regime": "human", "workspaces": "*"},
         "iat": now, "exp": now + 300, "jti": "j"}))
    return "vxd_" + body + "." + _b64u(hmac.new(DLG.encode(), body.encode("ascii"), hashlib.sha256).digest())


def _validate(client, token):
    return client.post("/internal/validate", json={"token": token},
                       headers={"X-Internal-Secret": INTERNAL,
                                validate_mod.ACCEPTS_DELEGATION_HEADER: validate_mod.ACCEPTS_DELEGATION_VALUE})


def _user(data):
    return User(id=42, email=ADDR, max_concurrent_bots=2, data=data)


PROVEN = pp.record_email_verified({}, ADDR, via="oidc:google", now=1.0)
PROVEN_OTHER = pp.record_email_verified({}, "old@example.com", via="magic_link", now=1.0)


@pytest.mark.parametrize("data,expected", [
    ({}, False),                         # made by an API call: never proven
    (PROVEN_OTHER, False),               # proven, but for an address the account no longer has
    (PROVEN, True),
], ids=["never-proven", "address-changed", "proven"])
def test_identity_of_states_whether_the_current_address_was_proven(data, expected):
    body = validate_mod.identity_of(_user(data), scopes=["bot"], is_admin=False)
    assert body["email_verified"] is expected


@pytest.mark.parametrize("data,expected", [({}, False), (PROVEN, True)], ids=["unproven", "proven"])
def test_a_workers_answer_carries_its_persons_proof(monkeypatch, data, expected):
    r = _validate(_client(monkeypatch, _user(data)), _delegation_token())
    assert r.status_code == 200, r.text
    assert r.json()["email_verified"] is expected


def test_the_answer_model_states_it_and_the_sealed_schema_declares_it():
    assert "email_verified" in validate_mod.ValidatedIdentity.model_fields
    body = validate_mod.identity_of(_user({}), scopes=["bot"], is_admin=False)
    out = validate_mod.ValidatedIdentity.model_validate({**body, "credential_class": "key"}).model_dump(
        mode="json", exclude_unset=True)
    assert out["email_verified"] is False
