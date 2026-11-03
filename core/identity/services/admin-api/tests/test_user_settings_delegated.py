"""The person's own settings door answers a worker acting for that person (Vexa-ai/vexa#1783).

A chat's agent reaches `settings` and `workspace_regime` through the one MCP, which forwards the
worker's delegation token (`vxd_…`) back through the gateway to these routes. Before this, the
routes looked the bearer up in the API-token table only, so every agent call answered
`403 Invalid API Key` — a new person's first chat failed on its very first look at their settings.

The token is verified the way `/internal/validate` verifies it (signature, expiry, live record,
account still exists). A worker with nobody in the loop may read, and may not change, a setting.

Offline: the DB session is a stub and the revocation store is patched; no Postgres, no Redis.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import delegation_revocation as revocation
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

DLG = "delegation-secret-for-the-settings-door"


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user

    def scalar_one(self):
        return self._user

    def scalars(self):
        return self

    def first(self):
        return self._user


class _Session:
    def __init__(self, users):
        self._users = users

    async def execute(self, stmt):
        params = stmt.compile().params
        uid = next((v for v in params.values() if isinstance(v, int)), None)
        return _Result(self._users.get(uid))

    def add(self, obj):
        pass

    async def commit(self):
        pass


@pytest.fixture()
def person():
    return User(id=47, email="new.person@example.com", data={})


@pytest.fixture()
def client(monkeypatch, person):
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-token-for-the-delegated-settings-test")
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")

    async def _admitted(jti):
        return True

    monkeypatch.setattr(revocation, "is_admitted", _admitted)
    app = create_app()

    async def _db():
        yield _Session({47: person})

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as c:
        yield c


def _worker(regime="human", subject="47", secret=DLG):
    return {"X-API-Key": delegation.mint_delegation(secret, subject=subject, regime=regime,
                                                     workspaces="*" if regime == "human" else ["47"],
                                                     unit="agent-47-chat-main")}


@pytest.mark.parametrize("path", ["/user/settings", "/user/workspace-regime"])
def test_a_new_persons_worker_reads_their_settings(client, path):
    r = client.get(path, headers=_worker())
    assert r.status_code == 200, r.text


def test_a_worker_with_the_person_in_the_loop_changes_one_setting(client, person):
    r = client.put("/user/settings", json={"setting": "mail_minutes", "value": "off"},
                   headers=_worker("human"))
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == {"mail_minutes": False}


def test_an_unwatched_worker_reads_but_does_not_change_a_setting(client):
    assert client.get("/user/settings", headers=_worker("autonomous")).status_code == 200
    r = client.put("/user/settings", json={"setting": "mail_minutes", "value": "off"},
                   headers=_worker("autonomous"))
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("headers", [
    _worker(secret="another-key"),          # forged
    _worker(subject="999"),                 # no such account
    {"X-API-Key": "vxd_not.a.token"},       # malformed
])
def test_a_token_identity_does_not_verify_names_nobody(client, headers):
    assert client.get("/user/settings", headers=headers).status_code in (401, 403)


def test_a_delegation_never_reaches_the_other_user_routes(client):
    """Only the person's settings door reads a worker's token; the rest of `/user/*` is unchanged."""
    assert client.get("/user/webhook", headers=_worker()).status_code == 403
