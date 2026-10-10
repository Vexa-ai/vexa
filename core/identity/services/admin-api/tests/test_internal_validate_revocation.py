"""A worker's delegation token stops resolving once agent-api revoked it — and an unreadable store
refuses delegation tokens, never API keys.

agent-api writes `vexa:delegation:revoked:<jti>` when the unit a token was minted for ends
(core/agent/control_plane/delegation_revocation.py). `/internal/validate` checks that key for every
`vxd_` bearer whose signature verified, and answers 503 when it cannot read the store, so a dead
Redis never reads as "not revoked".

No database and no Redis: `get_db` is overridden, and the store is the in-memory fake the conftest
installs (`revocation_store`), except where a test points the real client at nothing.
"""
import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import delegation_revocation
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import APIToken, User

INTERNAL = "internal-secret-for-tests"
DLG = "delegation-secret-for-tests"
KEY = "vxa_bot_test-key"
DECLARED = {validate_mod.ACCEPTS_DELEGATION_HEADER: validate_mod.ACCEPTS_DELEGATION_VALUE}


class _Result:
    def __init__(self, row=None, user=None):
        self._row, self._user = row, user

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._user


class _Session:
    """One API key and its account; a user by id for a delegation's subject."""

    def __init__(self, tokens, users):
        self._tokens, self._users = tokens, users

    async def execute(self, stmt):
        params = list(stmt.compile().params.values())
        key = next((v for v in params if isinstance(v, str)), None)
        if key is not None:
            return _Result(row=self._tokens.get(key))
        uid = next((v for v in params if isinstance(v, int)), None)
        return _Result(user=self._users.get(uid))

    async def commit(self):
        pass


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    user = User(id=42, email="ada@example.com", max_concurrent_bots=2, data={})
    tok = APIToken(token=KEY, user_id=42, scopes=["bot"], expires_at=None)
    app = create_app()

    async def _db():
        yield _Session({KEY: (tok, user)}, {42: user})

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _validate(client, token, *, declared=True):
    headers = {"X-Internal-Secret": INTERNAL, **(DECLARED if declared else {})}
    return client.post("/internal/validate", json={"token": token}, headers=headers)


def _mint(jti):
    return delegation.mint_delegation(DLG, subject="42", regime="autonomous", workspaces=["ws_1"],
                                      jti=jti)


# ── a token from a stopped unit is refused ──────────────────────────────────────────────────────

def test_a_token_whose_unit_ended_is_refused(client, revocation_store):
    revocation_store.revoke("unit-ended")
    r = _validate(client, _mint("unit-ended"))
    assert r.status_code == 401, r.text
    assert r.json() == {"detail": "Invalid delegation: revoked"}


def test_a_token_that_was_not_revoked_still_validates(client, revocation_store):
    revocation_store.revoke("some-other-unit")
    r = _validate(client, _mint("still-running"))
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == 42
    assert r.json()["delegation"] == {"regime": "autonomous", "workspaces": ["ws_1"]}


def test_revocation_is_per_token_not_per_person(client, revocation_store):
    """Ending one unit takes its token and no other dispatch's for the same person."""
    revocation_store.revoke("unit-a")
    assert _validate(client, _mint("unit-a")).status_code == 401
    assert _validate(client, _mint("unit-b")).status_code == 200


# ── the store unreachable: the vxd_ is refused, an API key is not touched ────────────────────────

def test_an_unreachable_store_refuses_the_delegation_token(client, revocation_store):
    revocation_store.down = True
    r = _validate(client, _mint("unknowable"))
    assert r.status_code == 503, r.text
    assert r.json() == {"detail": validate_mod.DELEGATION_REVOCATION_UNAVAILABLE}


def test_an_unreachable_store_does_not_touch_an_api_key(client, revocation_store):
    revocation_store.down = True
    r = _validate(client, KEY, declared=False)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == 42 and r.json()["scopes"] == ["bot"]
    assert revocation_store.reads == 0, "an API key never reads the revocation store"


def test_the_real_client_pointed_at_nothing_refuses(client, monkeypatch):
    """Not the fake: the module's own Redis client, aimed at a port nothing listens on. Whatever it
    raises, the delegation token is refused — the store being unreadable never means 'not revoked'."""
    monkeypatch.setattr(delegation_revocation, "_client", None)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    r = _validate(client, _mint("nobody-can-say"))
    assert r.status_code == 503, r.text
    assert _validate(client, KEY, declared=False).status_code == 200


# ── the store is asked only about a token whose signature verified ───────────────────────────────

def test_a_forged_token_is_refused_before_the_store_is_asked(client, revocation_store):
    revocation_store.down = True
    forged = delegation.mint_delegation("not-the-deployment-key", subject="42", jti="forged")
    r = _validate(client, forged)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid delegation: bad_signature"
    assert revocation_store.reads == 0


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_a_token_without_a_jti_is_refused(client, revocation_store):
    """A token that names no jti could never be revoked; delegation.v1 requires one."""
    now = int(time.time())
    canon = lambda d: json.dumps(d, separators=(",", ":"), sort_keys=True).encode()  # noqa: E731
    body = (_b64u(canon({"alg": "HS256", "typ": "vxdlg"})) + "." + _b64u(canon(
        {"sub": "42", "aud": "vexa-mcp", "scope": {"regime": "human", "workspaces": "*"},
         "iat": now, "exp": now + 300})))
    token = "vxd_" + body + "." + _b64u(hmac.new(DLG.encode(), body.encode(), hashlib.sha256).digest())
    r = _validate(client, token)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid delegation: malformed"
    assert revocation_store.reads == 0


def test_the_key_is_the_one_agent_api_writes():
    """Held equal with agent-api's writer by gate:fact-parity (delegation-revocation-key)."""
    assert delegation_revocation.REVOKED_PREFIX == "vexa:delegation:revoked:"
