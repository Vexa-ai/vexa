"""An empty-scope key has no access before a convergence and none after it (MIGRATION-0004).

0.12's read path denies a key whose `scopes` is empty: `get_current_user` 403s, and
`/internal/validate` answers `["legacy"]`, which names no route scope, so the gateway 403s too.
`scopes` is NOT NULL (models.py), so a NULL row cannot exist to be read either way. Only a row from
before the column existed (0.10, unscoped = allow-all) is grandfathered, in the pass that adds it.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

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
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        c.pg_url = pg_url
        yield c
    _dispose_async_engine()


def _user(client, email):
    return client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]


def _sql(client, statement, **params):
    """Run one statement; the rows it returns, fetched before the connection closes."""
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


def _access(client, token):
    user = client.get("/user/webhook", headers={"X-API-Key": token}).status_code
    v = client.post("/internal/validate", json={"token": token},
                    headers={"X-Internal-Secret": INTERNAL_SECRET})
    return user, v.json().get("scopes")


def test_an_empty_scope_key_has_no_access_before_and_after_a_convergence(client):
    uid = _user(client, "empty@example.test")
    _sql(client, "INSERT INTO api_tokens (token, user_id, scopes) VALUES ('vxa_empty_k', :u, '{}')", u=uid)

    assert _access(client, "vxa_empty_k") == (403, ["legacy"])
    _reconverge(client)                                   # every admin-api boot runs this
    assert _access(client, "vxa_empty_k") == (403, ["legacy"])
    assert _sql(client, "SELECT scopes FROM api_tokens WHERE token = 'vxa_empty_k'") == [([],)]


def test_a_null_scope_row_cannot_exist(client):
    uid = _user(client, "null@example.test")
    with pytest.raises(IntegrityError):
        _sql(client, "INSERT INTO api_tokens (token, user_id, scopes) VALUES ('vxa_null_k', :u, NULL)", u=uid)


def test_a_pre_scope_key_keeps_the_access_it_had(client):
    uid = _user(client, "legacy@example.test")
    _sql(client, "INSERT INTO api_tokens (token, user_id, scopes) VALUES ('vxa_legacy_k', :u, '{tx}')", u=uid)
    _sql(client, "ALTER TABLE api_tokens DROP COLUMN scopes")          # the 0.10 shape
    _reconverge(client)
    status, scopes = _access(client, "vxa_legacy_k")
    assert status == 200
    assert set(scopes) == {"bot", "tx", "browser"}
