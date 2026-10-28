"""MIGRATION-0015 on Postgres: webhook signing secrets written before this release — on a person
(`users.data`) and carried on a meeting (`meetings.data`) — are sealed in place at boot, a new one
is written sealed, and `/internal/validate` still answers the secret itself, so meeting-api keeps
signing deliveries every subscriber verifies.
"""
import hashlib
import hmac
import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api import at_rest
from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync
from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

SESSION_SECRET = "pg-test-terminal-session-secret-0123456789abcdef"

LEGACY = "whsec_set_by_the_customer_last_year"


def _sql(url, statement, **params):
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(statement), params)
            return result.all() if result.returns_rows else None
    finally:
        engine.dispose()


def _converge(url):
    engine = create_engine(url)
    try:
        ensure_schema_sync(engine, Base)
    finally:
        engine.dispose()


def _sign(secret, body, ts="1700000000"):
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch, tmp_path):
    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    monkeypatch.delenv(at_rest.KEY_FILE_ENV, raising=False)
    _converge(pg_url)                                     # the old release: no key, plaintext rows
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("VEXA_TERMINAL_SESSION_SECRET", SESSION_SECRET)
    app_db.configure(pg_async_url)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        c.pg_url = pg_url
        c.uid = c.post("/admin/users", headers=_admin(), json={"email": "hooks@example.test"}).json()["id"]
        c.key = c.post(f"/admin/users/{c.uid}/tokens", headers=_admin(), json={"scopes": ["bot"]}).json()["token"]
        _sql(pg_url, "UPDATE users SET data = data || CAST(:d AS jsonb) WHERE id = :u",
             d=json.dumps({"webhook_url": "https://hooks.example/in", "webhook_secret": LEGACY}), u=c.uid)
        _sql(pg_url, "INSERT INTO meetings (user_id, platform, status, data) VALUES (:u, 'google_meet', 'completed', "
                     "CAST(:d AS jsonb))",
             u=c.uid, d=json.dumps({"webhook_url": "https://hooks.example/in", "webhook_secret": LEGACY}))
        key = tmp_path / "at-rest.key"
        key.write_text(secrets.token_hex(32))
        monkeypatch.setenv(at_rest.KEY_FILE_ENV, str(key))  # the upgrade mounts the key
        yield c
    _dispose_async_engine()


def _validated_secret(c):
    r = c.post("/internal/validate", json={"token": c.key}, headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 200, r.text
    return r.json().get("webhook_secret")


def test_existing_secrets_are_sealed_in_place_and_webhooks_keep_verifying(client):
    c = client
    assert _validated_secret(c) == LEGACY                 # before the boot pass: plaintext, honoured
    _converge(c.pg_url)                                   # the upgraded admin-api's boot

    stored = [r[0] for r in _sql(c.pg_url, "SELECT data->>'webhook_secret' FROM users WHERE id = :u", u=c.uid)]
    stored += [r[0] for r in _sql(c.pg_url, "SELECT data->>'webhook_secret' FROM meetings WHERE user_id = :u", u=c.uid)]
    assert all(v.startswith("vxe1:") and LEGACY not in v for v in stored)
    dump = str(_sql(c.pg_url, "SELECT data FROM users")) + str(_sql(c.pg_url, "SELECT data FROM meetings"))
    assert LEGACY not in dump

    # the signed identity carries the same secret, and the meeting's copy opens to it: a delivery
    # signed from either verifies with what the subscriber holds
    body = b'{"event":"meeting.completed"}'
    assert _sign(_validated_secret(c), body) == _sign(LEGACY, body)
    assert at_rest.unseal(stored[1], at_rest.WEBHOOK_SECRET_PURPOSE) == LEGACY

    before = _sql(c.pg_url, "SELECT id, data FROM users ORDER BY id")
    _converge(c.pg_url)                                   # idempotent
    assert _sql(c.pg_url, "SELECT id, data FROM users ORDER BY id") == before


def test_a_new_secret_is_written_sealed_and_masked_back(client):
    c = client
    r = c.put("/user/webhook", headers={"X-API-Key": c.key},
              json={"webhook_url": "https://hooks.example/new", "webhook_secret": "whsec_brand_new_value_9z"})
    assert r.status_code == 200, r.text
    assert r.json()["webhook_secret_set"] and "brand_new" not in json.dumps(r.json())
    raw = _sql(c.pg_url, "SELECT data->>'webhook_secret' FROM users WHERE id = :u", u=c.uid)[0][0]
    assert raw.startswith("vxe1:")
    assert _validated_secret(c) == "whsec_brand_new_value_9z"
