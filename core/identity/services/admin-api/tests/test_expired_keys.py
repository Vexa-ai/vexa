"""#1868: expired API keys are not listed, and the one-off prune deletes only expired keys.

Before 0.13.4 Flows minted a 15-minute key per gateway read on the user's account; hundreds of expired
keys filled the key list. GET /admin/users/{id}/tokens now skips tokens whose expiry has passed, and
`admin_api.prune_expired_keys` removes them (dry run by default; --apply needs --max).
Same testcontainers-PG harness as test_admin_account.py.
"""
import asyncio
from datetime import datetime, timedelta

import pytest

try:
    from conftest import requires_docker
except Exception:  # pragma: no cover
    requires_docker = pytest.mark.skip(reason="no harness")


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from admin_api.app import db as app_db
    from admin_api.app.main import create_app
    from admin_api.schema.models import Base
    from admin_api.schema.sync import ensure_schema_sync
    from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _dispose_async_engine

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


def _admin():
    from test_stack_admin_api import _admin as a
    return a()


def _seed(client, pg_url):
    """One account: a key with no expiry, a valid expiring key, two long-expired keys, one key expired
    a minute ago (inside the grace), and an expired session. Returns (uid, {label: token id})."""
    from sqlalchemy import create_engine, text
    uid = client.post("/admin/users", headers=_admin(), json={"email": "keys@example.com"}).json()["id"]
    ids = {}
    for label in ("forever", "valid", "old1", "old2", "fresh_expired", "session"):
        body = {"name": label, "scopes": ["bot"]}
        if label != "forever":
            body["expires_in"] = 3600
        r = client.post(f"/admin/users/{uid}/tokens", headers=_admin(), json=body)
        assert r.status_code in (200, 201), r.text
        ids[label] = r.json()["id"]
    now = datetime.utcnow()
    eng = create_engine(pg_url)
    with eng.begin() as cx:
        for label, when in (("old1", now - timedelta(days=10)), ("old2", now - timedelta(days=2)),
                            ("fresh_expired", now - timedelta(minutes=1)), ("session", now - timedelta(days=3))):
            cx.execute(text("UPDATE api_tokens SET expires_at = :w WHERE id = :i"), {"w": when, "i": ids[label]})
        cx.execute(text("UPDATE api_tokens SET kind = 'session' WHERE id = :i"), {"i": ids["session"]})
    eng.dispose()
    return uid, ids


def _names(client, uid):
    r = client.get(f"/admin/users/{uid}/tokens", headers=_admin())
    assert r.status_code == 200, r.text
    return sorted(t["name"] for t in r.json())


def _remaining(pg_url, uid):
    from sqlalchemy import create_engine, text
    eng = create_engine(pg_url)
    with eng.connect() as cx:
        names = sorted(r[0] for r in cx.execute(text("SELECT name FROM api_tokens WHERE user_id = :u"), {"u": uid}))
    eng.dispose()
    return names


@requires_docker
def test_the_key_list_skips_expired_tokens(client, pg_url):
    uid, _ = _seed(client, pg_url)
    assert _names(client, uid) == ["forever", "valid"]


@requires_docker
def test_prune_dry_run_deletes_nothing(client, pg_url, pg_async_url):
    from admin_api import prune_expired_keys as p
    uid, _ = _seed(client, pg_url)
    lines = []
    assert asyncio.run(p.run(pg_async_url, apply=False, max_rows=None, grace_hours=1, batch=500, out=lines.append)) == 0
    assert lines[0].split(": ", 1)[1].startswith("2 rows on 1 accounts")
    assert len(_remaining(pg_url, uid)) == 6
    assert not any("key-" in l or "vxa_" in l for l in lines)       # never a token value


@requires_docker
def test_prune_refuses_more_than_max(client, pg_url, pg_async_url):
    from admin_api import prune_expired_keys as p
    uid, _ = _seed(client, pg_url)
    lines = []
    assert asyncio.run(p.run(pg_async_url, apply=True, max_rows=1, grace_hours=1, batch=500, out=lines.append)) == 2
    assert len(_remaining(pg_url, uid)) == 6


@requires_docker
def test_prune_deletes_only_long_expired_keys(client, pg_url, pg_async_url):
    from admin_api import prune_expired_keys as p
    uid, _ = _seed(client, pg_url)
    lines = []
    assert asyncio.run(p.run(pg_async_url, apply=True, max_rows=2, grace_hours=1, batch=1, out=lines.append)) == 0
    assert _remaining(pg_url, uid) == ["forever", "fresh_expired", "session", "valid"]
    assert lines[-1] == "deleted 2 expired keys; 0 still match"


def test_apply_needs_max():
    from admin_api import prune_expired_keys as p
    with pytest.raises(SystemExit):
        p.main(["--apply"])
