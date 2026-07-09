"""First-run bootstrap admin — /internal/instance + /internal/bootstrap-admin + the is_admin
surfacing on /internal/validate (the terminal admin gate's input).

Contract (first-run onboarding design, 2026-07-09): a fresh instance has NO admin; the first
sign-in claims the role exactly once (advisory-lock serialized); later sign-ins never claim.
The role lives in users.data["is_admin"] — no schema migration.

Same testcontainers-PG harness as the other suites (skips without docker).
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


def _mk_user(client, email):
    return client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]


def test_instance_and_bootstrap_gate_fail_closed(client):
    # internal edge only — no/wrong secret rejected
    assert client.get("/internal/instance").status_code == 403
    assert client.post("/internal/bootstrap-admin",
                       headers={"X-Internal-Secret": "wrong"},
                       json={"user_id": 1}).status_code == 403


def test_first_claim_wins_then_idempotent(client):
    a = _mk_user(client, "first-test@vexa.ai")
    b = _mk_user(client, "second-test@vexa.ai")

    # fresh instance: no admin
    r = client.get("/internal/instance", headers=_internal())
    assert r.status_code == 200 and r.json() == {"admin_exists": False}

    # first sign-in claims
    r = client.post("/internal/bootstrap-admin", headers=_internal(), json={"user_id": a})
    assert r.status_code == 200 and r.json() == {"claimed": True, "admin_exists": True}

    # instance now has an admin
    assert client.get("/internal/instance", headers=_internal()).json() == {"admin_exists": True}

    # a later user never claims; the admin re-claiming is a harmless no-op
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={"user_id": b}).json() == {"claimed": False, "admin_exists": True}
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={"user_id": a}).json() == {"claimed": False, "admin_exists": True}


def test_bootstrap_unknown_user_404(client):
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={"user_id": 99999}).status_code == 404
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={}).status_code == 404


def test_validate_surfaces_is_admin(client):
    uid = _mk_user(client, "admin-test@vexa.ai")
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]

    # before the claim: not an admin
    r = client.post("/internal/validate", headers=_internal(), json={"token": tok})
    assert r.status_code == 200 and r.json()["is_admin"] is False

    client.post("/internal/bootstrap-admin", headers=_internal(), json={"user_id": uid})
    r = client.post("/internal/validate", headers=_internal(), json={"token": tok})
    assert r.json()["is_admin"] is True


def test_setup_settings_key(client):
    # the wizard's durable step state rides the platform-settings CRUD under key "setup"
    r = client.put("/internal/settings/setup", headers=_internal(),
                   json={"models": "done", "transcription": "skipped", "completed": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["value"] == {"models": "done", "transcription": "skipped", "completed": "true"}
    # partial clear semantics hold
    r = client.put("/internal/settings/setup", headers=_internal(), json={"transcription": ""})
    assert r.json()["value"] == {"models": "done", "completed": "true"}
