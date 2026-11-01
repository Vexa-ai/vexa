"""#1868: expired API keys are not listed, and the one-off prune deletes only expired keys.

Before 0.13.4 Flows minted a 15-minute key per gateway read on the user's account; hundreds of expired
keys filled the key list. GET /admin/users/{id}/tokens now skips tokens whose expiry has passed, and
`admin_api.prune_expired_keys` removes them (dry run by default; --apply needs --max, which also caps the
total deleted; --before pins the cutoff the dry run printed; the dry run breaks matches down by key name
and scopes without token values or names a person typed).
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


def _expired_keys(pg_url, uid):
    from sqlalchemy import create_engine, text
    eng = create_engine(pg_url)
    with eng.connect() as cx:
        n = cx.execute(text("SELECT count(*) FROM api_tokens WHERE user_id = :u AND kind = 'key' "
                            "AND expires_at < now() at time zone 'utc'"), {"u": uid}).scalar()
    eng.dispose()
    return n


def _set_expiry(pg_url, token_id, when):
    from sqlalchemy import create_engine, text
    eng = create_engine(pg_url)
    with eng.begin() as cx:
        cx.execute(text("UPDATE api_tokens SET expires_at = :w WHERE id = :i"), {"w": when, "i": token_id})
    eng.dispose()


def _whole_seconds_ago(**kw):
    return (datetime.utcnow() - timedelta(**kw)).replace(microsecond=0)


@requires_docker
def test_prune_cap_holds_when_rows_appear_mid_run(client, pg_url, pg_async_url, monkeypatch):
    """--max 2 agreed on 2 rows; a new expired key appears before every delete batch. The run stops at 2
    deleted, says so, and leaves the late rows for the next dry run."""
    import uuid

    import sqlalchemy.ext.asyncio as sa_async
    from sqlalchemy import create_engine, text

    from admin_api import prune_expired_keys as p
    uid, _ = _seed(client, pg_url)
    long_ago = datetime.utcnow() - timedelta(days=5)
    real = sa_async.async_sessionmaker
    opened = {"n": 0, "inserted": 0}

    def patched(*a, **kw):
        factory = real(*a, **kw)

        def open_session():
            opened["n"] += 1
            # Sessions 1-2 are the survey and the breakdown; every later one gets a fresh match first
            # (at most 4, so a run that ignored the cap would finish and fail rather than loop forever).
            if opened["n"] > 2 and opened["inserted"] < 4:
                eng = create_engine(pg_url)
                with eng.begin() as cx:
                    cx.execute(text("INSERT INTO api_tokens (token, user_id, scopes, name, expires_at, kind) "
                                    "VALUES (:t, :u, ARRAY['bot']::text[], 'late', :w, 'key')"),
                               {"t": f"late-{uuid.uuid4().hex}", "u": uid, "w": long_ago})
                eng.dispose()
                opened["inserted"] += 1
            return factory()
        return open_session

    monkeypatch.setattr(sa_async, "async_sessionmaker", patched)
    lines = []
    assert asyncio.run(p.run(pg_async_url, apply=True, max_rows=2, grace_hours=1, batch=1, out=lines.append)) == 0
    assert lines[-1] == "deleted 2 expired keys (stopped at --max 2); 3 still match"
    assert _expired_keys(pg_url, uid) == 1 + 3          # fresh_expired (inside the grace) + 3 late rows


@requires_docker
def test_before_pins_the_cutoff(client, pg_url, pg_async_url):
    """The dry run prints its cutoff; --apply --before <that> deletes against it, not against a new
    now-1h: a key that expired after the pinned cutoff (but over an hour ago) stays."""
    from admin_api import prune_expired_keys as p
    uid, ids = _seed(client, pg_url)
    pinned = _whole_seconds_ago(hours=2)
    lines = []
    assert asyncio.run(p.run(pg_async_url, apply=False, max_rows=None, grace_hours=1, batch=500,
                             out=lines.append, before=pinned)) == 0
    stamp = pinned.isoformat() + "Z"
    assert f"cutoff: {stamp} (pass --before {stamp} to --apply to use the same cutoff)" in lines
    assert p.parse_before(stamp) == pinned
    _set_expiry(pg_url, ids["valid"], datetime.utcnow() - timedelta(minutes=90))   # after the pin, >1h ago
    out = []
    assert asyncio.run(p.run(pg_async_url, apply=True, max_rows=2, grace_hours=1, batch=500,
                             out=out.append, before=p.parse_before(stamp))) == 0
    assert out[-1] == "deleted 2 expired keys; 0 still match"
    assert _remaining(pg_url, uid) == ["forever", "fresh_expired", "session", "valid"]


@requires_docker
def test_a_key_expiring_exactly_at_the_cutoff_stays(client, pg_url, pg_async_url):
    from admin_api import prune_expired_keys as p
    uid, ids = _seed(client, pg_url)
    cutoff = _whole_seconds_ago(hours=2)
    _set_expiry(pg_url, ids["old2"], cutoff)                              # exactly at the cutoff
    _set_expiry(pg_url, ids["fresh_expired"], cutoff - timedelta(seconds=1))
    lines = []
    assert asyncio.run(p.run(pg_async_url, apply=True, max_rows=2, grace_hours=1, batch=500,
                             out=lines.append, before=cutoff)) == 0
    assert _remaining(pg_url, uid) == ["forever", "old2", "session", "valid"]


@requires_docker
def test_breakdown_shows_names_and_scopes_without_token_values(client, pg_url, pg_async_url):
    from admin_api import prune_expired_keys as p
    tokens, long_ago = [], datetime.utcnow() - timedelta(days=3)
    for i in range(3):
        uid = client.post("/admin/users", headers=_admin(), json={"email": f"acct{i}@example.com"}).json()["id"]
        for name, scopes in (("flows-gateway", ["bot", "browser", "tx"]),) + (((f"Alice laptop {i}", ["bot"]),) if i == 0 else ()):
            r = client.post(f"/admin/users/{uid}/tokens", headers=_admin(),
                            json={"name": name, "scopes": scopes, "expires_in": 3600})
            assert r.status_code in (200, 201), r.text
            tokens.append(r.json()["token"])
            _set_expiry(pg_url, r.json()["id"], long_ago)
    lines = []
    assert asyncio.run(p.run(pg_async_url, apply=False, max_rows=None, grace_hours=1, batch=500, out=lines.append)) == 0
    text_out = "\n".join(lines)
    assert "  'flows-gateway' [bot,browser,tx]: 3 rows on 3 accounts" in lines
    assert "  (other names) [bot]: 1 rows" in lines
    assert "laptop" not in text_out and "Alice" not in text_out        # a name one person typed is folded
    assert "@" not in text_out
    assert all(t not in text_out for t in tokens)                         # never a token value


def test_before_in_the_future_or_with_grace_is_refused():
    from admin_api import prune_expired_keys as p
    lines = []
    future = datetime.utcnow() + timedelta(hours=1)
    assert asyncio.run(p.run("postgresql+asyncpg://u:p@127.0.0.1:1/x", apply=True, max_rows=10, grace_hours=1,
                             batch=500, out=lines.append, before=future)) == 2
    assert lines[0].startswith("STOP: cutoff") and "future" in lines[0]
    with pytest.raises(SystemExit):
        p.main(["--before", "2026-11-01T09:00:00Z", "--grace-hours", "2"])
    with pytest.raises(SystemExit):
        p.main(["--before", "not-a-date"])
    assert p.parse_before("2026-11-01T10:00:00+01:00") == datetime(2026, 11, 1, 9, 0, 0)
    assert p.parse_before("2026-11-01T09:00:00Z") == datetime(2026, 11, 1, 9, 0, 0)


def test_apply_needs_max():
    from admin_api import prune_expired_keys as p
    with pytest.raises(SystemExit):
        p.main(["--apply"])
