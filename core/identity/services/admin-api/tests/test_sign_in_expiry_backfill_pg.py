"""B1 / MIGRATION-0011: a sign-in credential with no end gets the end its cookie had.

Sessions minted before sessions had a lifetime, and the `terminal-login` keys browsers were signed in
with before sessions existed, carry `expires_at` NULL: standing credentials, though the cookie that
carried them lived 30 days. Every admin-api boot runs the convergence, which bounds each one at
`created_at` + 30 days (already past for an old one, so it stops validating). A self-serve key, and a
row that already has an end, are left exactly as they were.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.key_store import hash_token
from admin_api.schema.models import Base
from admin_api.schema.sync import LOGIN_LIFETIME_SECONDS, ensure_schema_sync
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
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        c.pg_url = pg_url
        yield c
    _dispose_async_engine()


def _sql(client, statement, **params):
    engine = create_engine(client.pg_url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(statement), params)
            return result.all() if result.returns_rows else None
    finally:
        engine.dispose()


def _reconverge(client):
    engine = create_engine(client.pg_url)
    try:
        ensure_schema_sync(engine, Base)
    finally:
        engine.dispose()


def _row(client, uid, token, *, kind, name, scopes, created_at, expires_at=None):
    _sql(client, "INSERT INTO api_tokens (token, user_id, scopes, name, kind, created_at, expires_at) "
                 "VALUES (:t, :u, :s, :n, :k, :c, :e)",
         t=token, u=uid, s=scopes, n=name, k=kind, c=created_at, e=expires_at)


def _expiry(client, token):
    return _sql(client, "SELECT expires_at FROM api_tokens WHERE token_hash = :h OR token = :t",
                h=hash_token(token), t=token)[0][0]


def _validates(client, token):
    return client.post("/internal/validate", json={"token": token},
                       headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 200


def test_sign_in_credentials_with_no_end_end_with_their_cookie(client):
    uid = client.post("/admin/users", headers=_admin(), json={"email": "b1@example.test"}).json()["id"]
    now = datetime.utcnow().replace(microsecond=0)
    old, recent = now - timedelta(days=90), now - timedelta(days=2)
    browser = ["bot", "tx", "browser"]
    _row(client, uid, "vxa_browser_old_session", kind="session", name="terminal-login", scopes=browser, created_at=old)
    _row(client, uid, "vxa_browser_new_session", kind="session", name="terminal-login", scopes=browser, created_at=recent)
    _row(client, uid, "vxa_bot_old_login_key", kind="key", name="terminal-login", scopes=browser, created_at=old)
    _row(client, uid, "vxa_bot_new_login_key", kind="key", name="terminal-login", scopes=browser, created_at=recent)
    # controls: a person's own key, and a sign-in credential that already has an end
    _row(client, uid, "vxa_bot_script_key", kind="key", name="my-script", scopes=browser, created_at=old)
    own_end = now + timedelta(days=400)
    _row(client, uid, "vxa_browser_bounded", kind="session", name="terminal-login", scopes=browser,
         created_at=old, expires_at=own_end)

    assert _validates(client, "vxa_browser_old_session") and _validates(client, "vxa_bot_old_login_key")
    _reconverge(client)                                   # every admin-api boot runs this

    life = timedelta(seconds=LOGIN_LIFETIME_SECONDS)
    assert _expiry(client, "vxa_browser_old_session") == old + life
    assert _expiry(client, "vxa_bot_old_login_key") == old + life
    assert _expiry(client, "vxa_browser_new_session") == recent + life
    assert _expiry(client, "vxa_bot_new_login_key") == recent + life
    # the old ones' cookies are long gone, so they no longer validate; the recent ones still do
    assert not _validates(client, "vxa_browser_old_session")
    assert not _validates(client, "vxa_bot_old_login_key")
    assert _validates(client, "vxa_browser_new_session") and _validates(client, "vxa_bot_new_login_key")
    # controls are untouched
    assert _expiry(client, "vxa_bot_script_key") is None and _validates(client, "vxa_bot_script_key")
    assert _expiry(client, "vxa_browser_bounded") == own_end

    _reconverge(client)                                   # idempotent
    assert _expiry(client, "vxa_browser_new_session") == recent + life
