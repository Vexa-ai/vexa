"""`/internal/validate` against real Postgres while another transaction holds a lock (needs docker).

The companion of `test_validate_never_waits_on_writes.py`, which pins the statements on fakes. Here the
lock is real: a second connection holds the key's row (`FOR UPDATE`) or the whole table (`LOCK TABLE`)
and lets go after five seconds. Validate must not wait for it: with the row held it answers 200 at
once (the last-use stamp skips the locked row); with the table held it fails within its 1 s
`lock_timeout` (the gateway answers its typed 503) instead of hanging until the lock is released."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync
from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker
HOLD_S = 5.0


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


def _key(client):
    uid = client.post("/admin/users", headers=_admin(), json={"email": "ada@example.test"}).json()["id"]
    r = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin())
    assert r.status_code in (200, 201), r.text
    return r.json()["token"]


def _hold(pg_url, statement, ready):
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text(statement))
        ready.set()
        time.sleep(HOLD_S)
        tx.rollback()
    engine.dispose()


def _validate_while(client, statement):
    key = _key(client)
    engine = create_engine(client.pg_url)
    with engine.begin() as conn:   # a stale stamp, so validate does try to write it
        conn.execute(text("UPDATE api_tokens SET last_used_at = now() - interval '1 hour'"))
    engine.dispose()
    ready = threading.Event()
    t = threading.Thread(target=_hold, args=(client.pg_url, statement, ready))
    t.start()
    assert ready.wait(10)
    t0 = time.monotonic()
    r = client.post("/internal/validate", json={"token": key}, headers={"X-Internal-Secret": INTERNAL_SECRET})
    took = time.monotonic() - t0
    t.join()
    return r, took


def test_a_held_row_does_not_delay_the_answer(client):
    r, took = _validate_while(client, "SELECT id FROM api_tokens FOR UPDATE")
    assert r.status_code == 200, r.text
    assert took < 2.0, f"validate waited {took:.1f}s on a row lock"


def test_a_locked_table_fails_fast_instead_of_hanging(client):
    r, took = _validate_while(client, "LOCK TABLE api_tokens IN ACCESS EXCLUSIVE MODE")
    assert r.status_code >= 500
    assert took < 3.0, f"validate hung {took:.1f}s on a table lock"
