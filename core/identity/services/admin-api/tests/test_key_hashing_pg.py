"""MIGRATION-0013 on Postgres: a database from before this release, whose `api_tokens.token` holds
every key's value (NOT NULL, no hash column), converges to hashes, and every key it held keeps
working — through `/internal/validate` and through the self-serve tier — while no row holds a value.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.key_store import hash_token
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync
from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

OLD_KEYS = ("vxa_tx_customerTranscriptionKey000001", "vxa_bot_customerBotKeyMadeIn2025xyz9",
            "legacy-0.10-unprefixed-key-abcdef")


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


@pytest.fixture()
def legacy_db(pg_url):
    """A converged database put back into the pre-0013 shape, holding plaintext keys."""
    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    _converge(pg_url)
    _sql(pg_url, "ALTER TABLE api_tokens DROP COLUMN token_hash, DROP COLUMN token_hint")
    _sql(pg_url, "ALTER TABLE api_tokens ALTER COLUMN token SET NOT NULL")
    uid = _sql(pg_url, "INSERT INTO users (email, name, max_concurrent_bots, data, created_at) "
                       "VALUES ('rtb@example.test', 'rtb', 3, '{}'::jsonb, now()) RETURNING id")[0][0]
    for i, key in enumerate(OLD_KEYS):
        scopes = ["tx"] if "_tx_" in key else ["bot", "tx", "browser"]
        _sql(pg_url, "INSERT INTO api_tokens (token, user_id, scopes, name, kind, created_at) "
                     "VALUES (:t, :u, :s, :n, 'key', :c)",
             t=key, u=uid, s=scopes, n=f"customer-{i}", c=datetime(2025, 6, 1))
    return pg_url, uid


@pytest.fixture()
def client_after_upgrade(legacy_db, pg_async_url, monkeypatch):
    pg_url, uid = legacy_db
    _converge(pg_url)                                       # the new admin-api's boot
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    app_db.configure(pg_async_url)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        c.pg_url, c.uid = pg_url, uid
        yield c
    _dispose_async_engine()


def test_old_plaintext_keys_still_authenticate_after_hashing(client_after_upgrade):
    c = client_after_upgrade
    for key in OLD_KEYS:
        r = c.post("/internal/validate", json={"token": key}, headers={"X-Internal-Secret": INTERNAL_SECRET})
        assert r.status_code == 200, (key, r.text)
        assert r.json()["user_id"] == c.uid
        assert c.get("/user/webhook", headers={"X-API-Key": key}).status_code == 200, key


def test_no_row_holds_a_key_value_after_the_upgrade(client_after_upgrade):
    c = client_after_upgrade
    rows = _sql(c.pg_url, "SELECT token, token_hash, token_hint FROM api_tokens ORDER BY id")
    assert [r[0] for r in rows] == [None] * len(OLD_KEYS)
    assert [r[1] for r in rows] == [hash_token(k) for k in OLD_KEYS]
    dump = str(_sql(c.pg_url, "SELECT * FROM api_tokens"))
    assert not any(k in dump for k in OLD_KEYS)
    nullable = _sql(c.pg_url, "SELECT is_nullable FROM information_schema.columns "
                              "WHERE table_name = 'api_tokens' AND column_name = 'token'")[0][0]
    assert nullable == "YES"


def test_the_upgrade_is_idempotent_and_new_keys_are_hashed_from_birth(client_after_upgrade):
    c = client_after_upgrade
    before = _sql(c.pg_url, "SELECT id, token, token_hash, token_hint FROM api_tokens ORDER BY id")
    _converge(c.pg_url)                                     # the next boot
    assert _sql(c.pg_url, "SELECT id, token, token_hash, token_hint FROM api_tokens ORDER BY id") == before
    minted = c.post(f"/admin/users/{c.uid}/tokens", headers=_admin(), json={"scopes": ["bot"], "name": "new"})
    assert minted.status_code == 201, minted.text
    value = minted.json()["token"]
    row = _sql(c.pg_url, "SELECT token, token_hash FROM api_tokens WHERE id = :i", i=minted.json()["id"])[0]
    assert row == (None, hash_token(value))
    assert c.post("/internal/validate", json={"token": value},
                  headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 200


def test_a_plaintext_row_a_legacy_writer_adds_is_honoured_then_hashed(client_after_upgrade):
    """transcription-gateway writes `api_tokens` directly; until it ships hashes, its rows arrive in
    plaintext. They validate at once, and the next boot hashes them."""
    c = client_after_upgrade
    key = "vxa_tx_writtenByAnOldGateway00042"
    _sql(c.pg_url, "INSERT INTO api_tokens (token, user_id, scopes, kind) VALUES (:t, :u, ARRAY['tx'], 'key')",
         t=key, u=c.uid)
    ok = c.post("/internal/validate", json={"token": key}, headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert ok.status_code == 200
    _converge(c.pg_url)
    assert _sql(c.pg_url, "SELECT token, token_hash FROM api_tokens WHERE token_hash = :h",
                h=hash_token(key)) == [(None, hash_token(key))]
    assert c.post("/internal/validate", json={"token": key},
                  headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 200
