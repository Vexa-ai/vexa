"""The reserved-domain test users an app.dev admin signs in as (#1783 dev tooling).

`GET /admin/users/test-users` lists only `@example.test` accounts, admin token only."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app.main import create_app
from admin_api.app import db as app_db
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


def test_lists_only_reserved_domain_accounts_for_the_admin_token(client):
    for e in ("tester-ann@example.test", "real@acme.example", "Tester-Bo@Example.Test"):
        client.post("/admin/users", headers=_admin(), json={"email": e})
    r = client.get("/admin/users/test-users", headers=_admin())
    assert r.status_code == 200
    assert sorted(u["email"].lower() for u in r.json()) == ["tester-ann@example.test", "tester-bo@example.test"]
    assert client.get("/admin/users/test-users").status_code in (401, 403)
