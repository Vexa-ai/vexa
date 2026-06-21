"""O-STACK-3 — Admin-api backing-stack eval (testcontainers-PG + FastAPI TestClient).

The golden identity flow against an ephemeral Postgres, asserting the REAL admin-api surface
(carved into v0.12 at admin_api.app.main):

  create user → mint scoped token → /internal/validate (correct user_id + scopes + webhook
  config; HMAC internal-secret REQUIRED + FAIL-CLOSED) → revoke → expired-token rejected →
  invalid scope 422 → admin-tier auth enforced.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker

pytestmark = requires_docker

ADMIN_TOKEN = "test-admin-token"
INTERNAL_SECRET = "test-internal-secret"


def _dispose_async_engine():
    """Best-effort dispose of the configured async engine (teardown hygiene; never fails a test)."""
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(app_db.get_engine().dispose())
        loop.close()
    except Exception:
        pass


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    # Converge the schema synchronously (psycopg URL), then point the async app at the same DB.
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()

    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")

    app_db.configure(pg_async_url)
    app = create_app()
    with TestClient(app) as c:
        yield c

    _dispose_async_engine()


def _admin(h=None):
    return {"X-Admin-API-Key": ADMIN_TOKEN, **(h or {})}


def test_golden_identity_flow(client):
    # 1. create user (admin tier)
    r = client.post("/admin/users", headers=_admin(),
                    json={"email": "bob@vexa.ai", "name": "Bob", "max_concurrent_bots": 5})
    assert r.status_code in (200, 201), r.text
    user_id = r.json()["id"]

    # 2. mint a scoped token (bot + tx)
    r = client.post(f"/admin/users/{user_id}/tokens?scopes=bot,tx", headers=_admin())
    assert r.status_code == 201, r.text
    tok = r.json()
    token_value = tok["token"]
    token_id = tok["id"]
    assert token_value.startswith("vxa_bot_")
    assert set(tok["scopes"]) == {"bot", "tx"}

    # 2b. set a webhook (user tier — writes user.data JSONB)
    r = client.put("/user/webhook", headers={"X-API-Key": token_value},
                   json={"webhook_url": "https://example.com/hook",
                         "webhook_secret": "shh",
                         "webhook_events": {"meeting.completed": True}})
    assert r.status_code == 200, r.text

    # 3. /internal/validate — correct secret → user_id + scopes + webhook config surfaced
    r = client.post("/internal/validate", headers={"X-Internal-Secret": INTERNAL_SECRET},
                    json={"token": token_value})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["user_id"] == user_id
    assert set(v["scopes"]) == {"bot", "tx"}
    assert v["max_concurrent"] == 5
    assert v["email"] == "bob@vexa.ai"
    assert v["webhook_url"] == "https://example.com/hook"
    assert v["webhook_secret"] == "shh"
    assert v["webhook_events"] == {"meeting.completed": True}

    # 4. revoke → the same token no longer validates
    r = client.delete(f"/admin/tokens/{token_id}", headers=_admin())
    assert r.status_code == 204, r.text
    r = client.post("/internal/validate", headers={"X-Internal-Secret": INTERNAL_SECRET},
                    json={"token": token_value})
    assert r.status_code == 401


def test_internal_validate_requires_secret(client):
    """HMAC internal-secret REQUIRED — a missing/wrong X-Internal-Secret is rejected 403."""
    # Mint a token to validate.
    user_id = client.post("/admin/users", headers=_admin(),
                          json={"email": "c@vexa.ai"}).json()["id"]
    token_value = client.post(f"/admin/users/{user_id}/tokens?scope=bot",
                              headers=_admin()).json()["token"]

    # Missing secret → 403.
    r = client.post("/internal/validate", json={"token": token_value})
    assert r.status_code == 403
    # Wrong secret → 403.
    r = client.post("/internal/validate", headers={"X-Internal-Secret": "nope"},
                    json={"token": token_value})
    assert r.status_code == 403


def test_internal_validate_fail_closed_when_secret_unset(pg_url, pg_async_url, monkeypatch):
    """FAIL-CLOSED: INTERNAL_API_SECRET unset + not dev mode → 503 (never silently allow)."""
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()

    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)

    app_db.configure(pg_async_url)
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/internal/validate", json={"token": "anything"})
        assert r.status_code == 503
    _dispose_async_engine()


def test_expired_token_rejected(client):
    """A token past expires_at must be rejected 401 by /internal/validate."""
    user_id = client.post("/admin/users", headers=_admin(),
                          json={"email": "d@vexa.ai"}).json()["id"]
    # expires_in must be > 0 to set an expiry; use a tiny TTL and let it lapse.
    token_value = client.post(f"/admin/users/{user_id}/tokens?scope=bot&expires_in=1",
                              headers=_admin()).json()["token"]
    import time
    time.sleep(1.5)
    r = client.post("/internal/validate", headers={"X-Internal-Secret": INTERNAL_SECRET},
                    json={"token": token_value})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()


def test_invalid_scope_422(client):
    """Minting a token with an unknown scope → 422."""
    user_id = client.post("/admin/users", headers=_admin(),
                          json={"email": "e@vexa.ai"}).json()["id"]
    r = client.post(f"/admin/users/{user_id}/tokens?scope=superadmin", headers=_admin())
    assert r.status_code == 422
    assert "Invalid scope" in r.json()["detail"]


def test_admin_tier_auth_enforced(client):
    """The admin tier rejects a missing/wrong X-Admin-API-Key (403)."""
    r = client.post("/admin/users", json={"email": "f@vexa.ai"})           # no key
    assert r.status_code == 403
    r = client.post("/admin/users", headers={"X-Admin-API-Key": "wrong"},  # bad key
                    json={"email": "f@vexa.ai"})
    assert r.status_code == 403
