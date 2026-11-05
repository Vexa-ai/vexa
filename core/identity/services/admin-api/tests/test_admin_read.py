"""The admin READ surface (`app/admin_read.py`): four answers, one key that opens nothing else.

What these tests pin, row by row against the surface's contract:

- each route answers 200 with exactly its item shape for the read key;
- the read key is refused (403) on the admin surface (`POST /admin/users`, a user read), and every
  write verb under `/admin/read/` is refused (403) whoever asks — the admin token included;
- a window wider than one day is refused (400);
- the query runs read-only with a 30 s statement timeout, and it is ONE query per request;
- `meetings.data` is never selected whole.
"""
import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from admin_api.app import admin_read
from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base, Meeting, User
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

READ_KEY = "vxr_test-read-key-0123456789abcdefghijklmnopqrstuv"


def _read(key=READ_KEY):
    return {"Authorization": f"Bearer {key}"}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def engine(pg_url):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    yield sync_engine
    sync_engine.dispose()


@pytest.fixture()
def client(engine, pg_async_url, monkeypatch):
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("ADMIN_READ_TOKEN_SHA256", hashlib.sha256(READ_KEY.encode()).hexdigest())
    monkeypatch.delenv("RUNTIME_API_URL", raising=False)
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _user(engine, email, created_at):
    with Session(engine) as s:
        u = User(email=email, created_at=created_at, data={})
        s.add(u)
        s.commit()
        return u.id


def _meeting(engine, user_id, *, status="completed", start=None, end=None, data=None, native=None):
    with Session(engine) as s:
        m = Meeting(user_id=user_id, platform="google_meet", platform_specific_id=native,
                    status=status, start_time=start, end_time=end, data=data or {})
        s.add(m)
        s.commit()
        return m.id


# ── 200 with the shape, for the read key ─────────────────────────────────────────────────────────

def test_signups_answers_new_users_in_order_with_exactly_their_three_fields(client, engine):
    now = _utcnow()
    _user(engine, "old@vexa.ai", now - timedelta(hours=30))
    b = _user(engine, "b@vexa.ai", now - timedelta(minutes=30))
    a = _user(engine, "a@vexa.ai", now - timedelta(hours=2))

    r = client.get("/admin/read/signups", params={"since": _z(now - timedelta(hours=24)),
                                                  "limit": "200"}, headers=_read())
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["user_id"] for i in items] == [a, b]
    for item in items:
        assert set(item) == {"user_id", "email", "created_at"}
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", item["created_at"])
    assert items[0]["email"] == "a@vexa.ai"


def test_signups_honours_the_limit_and_refuses_more_than_500(client, engine):
    now = _utcnow()
    for n in range(3):
        _user(engine, f"u{n}@vexa.ai", now - timedelta(minutes=10 - n))
    since = _z(now - timedelta(hours=1))
    r = client.get("/admin/read/signups", params={"since": since, "limit": "2"}, headers=_read())
    assert r.status_code == 200 and len(r.json()["items"]) == 2
    assert client.get("/admin/read/signups", params={"since": since, "limit": "501"},
                      headers=_read()).status_code == 422


def test_usage_answers_bot_minutes_per_user_for_the_day(client, engine):
    uid = _user(engine, "user@vexa.ai", _utcnow() - timedelta(days=30))
    day = (_utcnow() - timedelta(days=1)).date()
    noon = datetime(day.year, day.month, day.day, 12)
    _meeting(engine, uid, start=noon, end=noon + timedelta(minutes=30), native="a")
    _meeting(engine, uid, start=noon + timedelta(hours=1), end=noon + timedelta(hours=1, minutes=15),
             status="failed", native="b")
    _meeting(engine, uid, start=None, end=noon, status="failed", native="c")    # never in the call
    _meeting(engine, uid, start=noon + timedelta(days=1), end=noon + timedelta(days=1, minutes=5),
             native="d")                                                         # another day

    r = client.get("/admin/read/usage", params={"day": day.isoformat()}, headers=_read())
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [{"user_id": uid, "email": "user@vexa.ai", "day": day.isoformat(),
                                  "minutes": 45.0, "meetings": 2}]


def test_usage_without_a_day_is_today_and_a_future_day_is_refused(client):
    r = client.get("/admin/read/usage", headers=_read())
    assert r.status_code == 200 and r.json() == {"items": []}
    tomorrow = (_utcnow() + timedelta(days=1)).date().isoformat()
    assert client.get("/admin/read/usage", params={"day": tomorrow}, headers=_read()).status_code == 400
    assert client.get("/admin/read/usage", params={"day": "yesterday"}, headers=_read()).status_code == 400


def test_bot_outcomes_answer_ended_runs_with_the_raw_reason_and_grade_unknown(client, engine):
    uid = _user(engine, "user@vexa.ai", _utcnow() - timedelta(days=30))
    now = _utcnow()
    done = _meeting(engine, uid, start=now - timedelta(hours=2), end=now - timedelta(hours=1),
                    data={"completion_reason": "stopped", "bot_logs": ["x" * 100],
                          "transcript_viewers": ["someone"]}, native="a")
    refused = _meeting(engine, uid, status="failed", end=now - timedelta(minutes=5),
                       data={"reason": "AUTH_SESSION_MISSING"}, native="b")
    _meeting(engine, uid, status="active", start=now - timedelta(minutes=30), native="c")  # running
    _meeting(engine, uid, end=now - timedelta(hours=30), native="d")                     # outside

    r = client.get("/admin/read/bot-outcomes", params={"since": _z(now - timedelta(hours=24)),
                                                       "limit": "500"}, headers=_read())
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["meeting_id"] for i in items] == [done, refused]
    assert items[0]["exit_reason"] == "stopped"
    assert items[1]["exit_reason"] == "AUTH_SESSION_MISSING"
    for item in items:
        assert set(item) == {"meeting_id", "user_id", "grade", "exit_reason", "ended_at"}
        assert item["grade"] == "unknown"   # the product's grading is not in this release (#1994)
        assert item["user_id"] == uid
        assert item["ended_at"].endswith("Z")


def test_health_answers_the_four_checks(client, redis_client, monkeypatch):
    host, port = redis_client.connection_pool.connection_kwargs["host"], \
        redis_client.connection_pool.connection_kwargs["port"]
    monkeypatch.setenv("REDIS_URL", f"redis://{host}:{port}/0")
    from admin_api.app import signin_links
    monkeypatch.setattr(signin_links, "_client", None)

    r = client.get("/admin/read/health", headers=_read())
    assert r.status_code == 200, r.text
    checks = {c["name"]: c for c in r.json()["checks"]}
    assert list(checks) == ["api", "db", "redis", "bot_pool"]
    assert checks["api"]["status"] == "ok"
    assert checks["db"]["status"] == "ok"
    assert checks["redis"]["status"] == "ok"
    assert checks["bot_pool"]["status"] == "unknown"   # RUNTIME_API_URL unset: a missing fact
    for c in checks.values():
        assert set(c) == {"name", "status", "checked_at"} and c["checked_at"].endswith("Z")

    monkeypatch.setenv("RUNTIME_API_URL", "http://127.0.0.1:9")   # nothing listens there
    checks = {c["name"]: c for c in client.get("/admin/read/health", headers=_read()).json()["checks"]}
    assert checks["bot_pool"]["status"] == "down"


# ── the key opens these four routes and nothing else ─────────────────────────────────────────────

def test_the_read_key_is_refused_on_the_admin_surface(client, engine):
    uid = _user(engine, "user@vexa.ai", _utcnow())
    as_both = {**_read(), "X-Admin-API-Key": READ_KEY}
    r = client.post("/admin/users", headers=as_both, json={"email": "new@vexa.ai"})
    assert r.status_code == 403, r.text
    assert client.get(f"/admin/users/{uid}", headers=as_both).status_code == 403
    assert client.post(f"/admin/users/{uid}/tokens", headers=as_both).status_code == 403
    # and it is not a user API key either
    assert client.get("/user/settings", headers={"X-API-Key": READ_KEY}).status_code in (401, 403)


@pytest.mark.parametrize("verb", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/admin/read/signups", "/admin/read/usage",
                                  "/admin/read/bot-outcomes", "/admin/read/health",
                                  "/admin/read/anything"])
def test_every_write_verb_under_the_read_prefix_is_refused_whoever_asks(client, verb, path):
    for headers in (_read(), _admin(), {}):
        r = client.request(verb, path, headers=headers, json={})
        assert r.status_code == 403, (verb, path, headers, r.text)


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong-key"},
    {"Authorization": READ_KEY},                    # no scheme
    {"Authorization": f"Basic {READ_KEY}"},
    {"Authorization": f"Bearer {ADMIN_TOKEN}"},     # the admin token is not a read key
    {"X-Admin-API-Key": ADMIN_TOKEN},
])
def test_without_the_read_key_every_read_is_refused(client, headers):
    since = _z(_utcnow() - timedelta(hours=1))
    for path, params in (("/admin/read/signups", {"since": since}), ("/admin/read/usage", {}),
                         ("/admin/read/bot-outcomes", {"since": since}), ("/admin/read/health", {})):
        assert client.get(path, params=params, headers=headers).status_code == 403, (path, headers)


def test_unconfigured_or_configured_as_the_admin_token_the_surface_stays_shut(client, monkeypatch):
    monkeypatch.delenv("ADMIN_READ_TOKEN_SHA256")
    assert client.get("/admin/read/health", headers=_read()).status_code == 503
    monkeypatch.setenv("ADMIN_READ_TOKEN_SHA256", "not-a-hash")
    assert client.get("/admin/read/health", headers=_read()).status_code == 503
    monkeypatch.setenv("ADMIN_READ_TOKEN_SHA256", hashlib.sha256(ADMIN_TOKEN.encode()).hexdigest())
    assert client.get("/admin/read/health", headers=_read(ADMIN_TOKEN)).status_code == 503


# ── one day at most ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/admin/read/signups", "/admin/read/bot-outcomes"])
def test_a_two_day_window_is_refused(client, path):
    now = _utcnow()
    assert client.get(path, params={"since": _z(now - timedelta(days=2))},
                      headers=_read()).status_code == 400
    assert client.get(path, params={"since": "not-a-time"}, headers=_read()).status_code == 400
    assert client.get(path, params={"since": _z(now + timedelta(hours=1))},
                      headers=_read()).status_code == 400
    # exactly the reader's first window (24 h back, sent a moment ago) is inside
    assert client.get(path, params={"since": _z(now - timedelta(hours=24))},
                      headers=_read()).status_code == 200


# ── read-only, 30 s, one query, named paths ──────────────────────────────────────────────────────

def test_the_query_runs_read_only_under_a_30s_timeout(engine, pg_async_url):
    app_db.configure(pg_async_url)

    async def probe(sql):
        async with app_db.session() as s:
            return await admin_read._read(s, sql, {})

    async def both():
        try:
            row = (await probe("SELECT current_setting('transaction_read_only') AS ro, "
                               "current_setting('statement_timeout') AS t"))[0]
            try:
                await probe("INSERT INTO users (email, data) VALUES ('w@vexa.ai', '{}')")
                refused = None
            except Exception as e:  # noqa: BLE001 — the refusal IS the assertion
                refused = str(e)
            return row, refused
        finally:
            await app_db.get_engine().dispose()

    row, refused = asyncio.run(both())
    assert (row["ro"], row["t"]) == ("on", "30s")
    assert refused is not None and "read-only" in refused.lower(), refused
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM users WHERE email = 'w@vexa.ai'")).scalar() == 0


def test_each_request_is_one_query(client, engine):
    # Warm the pool first: a NEW connection's dialect handshake (version, schema) is not a request's.
    assert client.get("/admin/read/health", headers=_read()).status_code == 200
    statements = []
    target = app_db.get_engine().sync_engine

    def record(conn, cursor, statement, *args):
        statements.append(statement.strip())
    event.listen(target, "before_cursor_execute", record)
    try:
        since = _z(_utcnow() - timedelta(hours=1))
        for path, params in (("/admin/read/signups", {"since": since}), ("/admin/read/usage", {}),
                             ("/admin/read/bot-outcomes", {"since": since}),
                             ("/admin/read/health", {})):
            statements.clear()
            assert client.get(path, params=params, headers=_read()).status_code == 200
            queries = [s for s in statements if not s.upper().startswith(("SET ", "BEGIN", "COMMIT",
                                                                         "ROLLBACK", "SELECT PG_",
                                                                         "SHOW "))]
            assert len(queries) == 1, (path, statements)
    finally:
        event.remove(target, "before_cursor_execute", record)


def test_meetings_data_is_only_ever_read_by_named_path():
    for sql in (admin_read.USAGE_SQL, admin_read.BOT_OUTCOMES_SQL, admin_read.SIGNUPS_SQL):
        for hit in re.finditer(r"\bdata\b", sql):
            assert sql[hit.end():].lstrip().startswith("->>"), sql
