"""`/internal/validate` answers without waiting on a write, and fails fast when the database is stuck.

2026-10-13 11:25 UTC: every container was healthy and validate took 23–38 s (normally 3–5 ms) while
Postgres showed sessions waiting on a lock and on IO under a loaded host. Every request a person makes
waits on this route, so the gateway answered `503 Authentication temporarily unavailable` in the
middle of a chat. Validate wrote `api_tokens.last_used_at` and committed on EVERY call, so concurrent
calls with one key queued behind each other's row lock and synchronous commit.

Now: the last-use stamp is advisory — written at most once a minute per key, without waiting for a
row lock another transaction holds (`FOR UPDATE SKIP LOCKED`, a 100 ms `lock_timeout`, no wait for
the commit to reach disk), and a failure to write it never fails the answer. The read itself carries
a `statement_timeout` and a `lock_timeout`, so a stuck database fails in seconds (the gateway then
answers its typed 503 at once) instead of holding the request for half a minute.

No database: `get_db` is overridden with fake sessions (the real-Postgres check is
`test_validate_lock_wait_pg.py`, which needs docker)."""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from admin_api.key_store import stored_fields
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import APIToken, User

INTERNAL = "internal-secret-for-tests"
KEY = "vxa_bot_test-key"


class _Result:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row


class _Dialect:
    def __init__(self, name):
        self.name = name


class _Bind:
    def __init__(self, name):
        self.dialect = _Dialect(name)


class _Session:
    def __init__(self, tok, user, *, dialect=None, commit_raises=None):
        self.row, self.sql, self.commits, self.rollbacks = (tok, user), [], 0, 0
        self._dialect, self._commit_raises = dialect, commit_raises

    def get_bind(self):
        if self._dialect is None:
            raise AttributeError("no bind")
        return _Bind(self._dialect)

    async def execute(self, stmt, params=None):
        text = str(stmt)
        self.sql.append(" ".join(text.split()))
        return _Result(row=self.row if "FROM api_tokens JOIN users" in " ".join(text.split()) else None)

    async def commit(self):
        if self._commit_raises:
            raise self._commit_raises
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _client(monkeypatch, session):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    app = create_app()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _pair(last_used=None):
    user = User(id=42, email="ada@example.com", max_concurrent_bots=2, data={})
    tok = APIToken(id=7, **stored_fields(KEY), user_id=42, scopes=["bot"], last_used_at=last_used)
    return tok, user


def _validate(client):
    return client.post("/internal/validate", json={"token": KEY}, headers={"X-Internal-Secret": INTERNAL})


def test_a_failed_last_use_write_never_fails_the_answer(monkeypatch):
    """A lock timeout (or any error) on the stamp is logged and rolled back; the key still validates."""
    session = _Session(*_pair(), commit_raises=RuntimeError("canceling statement due to lock timeout"))
    r = _validate(_client(monkeypatch, session))
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == 42 and session.rollbacks == 1


def test_a_key_used_within_the_last_minute_is_not_written_again(monkeypatch):
    session = _Session(*_pair(last_used=datetime.utcnow() - timedelta(seconds=5)))
    assert _validate(_client(monkeypatch, session)).status_code == 200
    assert session.commits == 0
    assert not any(s.startswith("UPDATE") for s in session.sql)


def test_a_stale_last_use_is_still_stamped(monkeypatch):
    tok, user = _pair(last_used=datetime.utcnow() - timedelta(minutes=5))
    session = _Session(tok, user)
    assert _validate(_client(monkeypatch, session)).status_code == 200
    assert session.commits == 1


def test_on_postgres_the_read_is_bounded_and_the_stamp_never_waits(monkeypatch):
    session = _Session(*_pair(), dialect="postgresql")
    assert _validate(_client(monkeypatch, session)).status_code == 200, session.sql
    select_at = next(i for i, s in enumerate(session.sql) if "FROM api_tokens JOIN users" in s)
    before = session.sql[:select_at]
    assert any("statement_timeout" in s for s in before) and any("lock_timeout" in s for s in before)
    update = next(s for s in session.sql if s.startswith("UPDATE api_tokens"))
    assert "SKIP LOCKED" in update
    assert any("synchronous_commit" in s for s in session.sql)
    assert validate_mod.LAST_USED_RESOLUTION == timedelta(seconds=60)
