"""The two identity doors the flows tier reaches through the gateway's service door.

Flows used to hold the admin key to put a person on the platform and to mint a gateway token for
any user. It holds neither now: it creates a person through `POST /internal/users` (an id and an
address back, nothing else) and reads nobody's token — the gateway's door asks
`GET /internal/users/{id}/identity` and signs that identity onto the meetings call itself.
Both are internal-tier routes: the admin key is not a way in, and neither is no secret.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _internal():
    return {"X-Internal-Secret": INTERNAL_SECRET}


# ── POST /internal/users ────────────────────────────────────────────────────────────────────────

def test_the_internal_tier_puts_a_person_on_the_platform_and_learns_only_their_id(client):
    r = client.post("/internal/users", headers=_internal(),
                    json={"email": "Ann.Smith@Acme.test", "name": "Ann"})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert set(body) == {"id", "email"}
    assert body["email"] == "ann.smith@acme.test"
    # One point a person enters: the same account the admin door sees, never a second one.
    again = client.post("/internal/users", headers=_internal(), json={"email": "ann.smith@acme.test"})
    assert again.status_code == 200 and again.json()["id"] == body["id"]
    seen = client.get("/admin/users/email/ann.smith@acme.test", headers=_admin())
    assert seen.status_code == 200 and seen.json()["id"] == body["id"]


def test_creating_a_person_needs_the_internal_tier_and_an_address(client):
    assert client.post("/internal/users", json={"email": "a@acme.test"}).status_code == 403
    assert client.post("/internal/users", headers={"X-Internal-Secret": "wrong"},
                       json={"email": "a@acme.test"}).status_code == 403
    assert client.post("/internal/users", headers=_admin(),
                       json={"email": "a@acme.test"}).status_code == 403
    assert client.post("/internal/users", headers=_internal(), json={"email": "nobody"}).status_code == 422
    assert client.get("/admin/users/email/a@acme.test", headers=_admin()).status_code == 404


# ── GET /internal/users/{id}/identity ──────────────────────────────────────────────────────────

def test_a_person_s_identity_is_what_the_edge_stamps_for_their_key_without_scopes(client):
    uid = client.post("/internal/users", headers=_internal(), json={"email": "bo@acme.test"}).json()["id"]
    assert client.post(f"/internal/users/{uid}/memberships", headers=_internal(),
                       json={"workspace_id": "ws-1", "role": "contributor"}).status_code == 200
    r = client.get(f"/internal/users/{uid}/identity", headers=_internal())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == uid and body["email"] == "bo@acme.test"
    assert body["scopes"] == [], "the door grants scopes; identity grants none here"
    assert body["max_concurrent"] == 3
    assert body["writable_workspaces"] == ["ws-1"]
    assert "credential_class" not in body
    for leaked in ("token", "tokens", "data", "webhook_secret"):
        assert leaked not in body


def test_identity_is_internal_tier_only_and_never_for_a_workspace_principal(client):
    uid = client.post("/internal/users", headers=_internal(), json={"email": "cy@acme.test"}).json()["id"]
    assert client.get(f"/internal/users/{uid}/identity").status_code == 403
    assert client.get(f"/internal/users/{uid}/identity", headers=_admin()).status_code == 403
    assert client.get("/internal/users/999999/identity", headers=_internal()).status_code == 404
    principal = client.post("/internal/principals/workspace", headers=_internal(),
                            json={"workspace_id": "ws-room", "bills_to": uid})
    assert principal.status_code == 200, principal.text
    pid = principal.json()["id"]
    assert client.get(f"/internal/users/{pid}/identity", headers=_internal()).status_code == 404
